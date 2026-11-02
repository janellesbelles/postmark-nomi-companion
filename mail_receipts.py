"""Read-only Postmark delivery checks and a profile-independent receipt ledger."""
from __future__ import annotations
import datetime as dt
import re
import time
import urllib.parse
from mail_pipeline import AtomicState, request_json


class DeliveryHistoryError(RuntimeError):
    pass


def delivered_replies(handle, *, request=None):
    """Scan all outbox pages; only exact delivered reply edges are evidence."""
    request = request or request_json
    if not re.fullmatch(r'[A-Za-z0-9-]+', handle):
        raise DeliveryHistoryError('A valid resident handle is required for delivery history.')
    receipts, seen = {}, set()
    for offset in range(0, 10000, 100):
        try:
            page = request('https://postmark.town/api/mail/'+urllib.parse.quote(handle,safe='')+
                           f'?box=outbox&limit=100&offset={offset}')
            rows = page if isinstance(page,list) else page.get('letters') if isinstance(page,dict) else None
            if not isinstance(rows,list) or len(rows)>100:
                raise ValueError('Unsupported mail history shape')
            for row in rows:
                if not isinstance(row,dict) or row.get('from') != handle or not isinstance(row.get('id'),str) or not row['id']:
                    raise ValueError('Mailbox identity could not be verified')
                if row['id'] in seen:
                    raise ValueError('Overlapping history pages; retry a fresh check')
                seen.add(row['id'])
                thread, stamp, recipient = row.get('thread'),row.get('delivered_at'),row.get('to')
                if not thread or not stamp:
                    continue  # introductions or unconfirmed delivery are not reply receipts
                if not all(isinstance(v,str) and v for v in (thread,stamp,recipient)):
                    raise ValueError('Malformed delivered reply metadata')
                when = dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if when.tzinfo is None:
                    raise ValueError('Delivery time must include its timezone')
                receipt={'id':row['id'],'from':handle,'to':recipient,'thread':thread,
                         'delivered_at':stamp,'source':'postmark_outbox'}
                if thread not in receipts or when > dt.datetime.fromisoformat(receipts[thread]['delivered_at'].replace('Z','+00:00')):
                    receipts[thread]=receipt
            if len(rows)<100 or isinstance(page,dict) and page.get('complete') is True:
                if isinstance(page,dict) and page.get('complete') is False:
                    raise ValueError('Incomplete history response')
                return receipts
        except Exception:
            raise DeliveryHistoryError('Could not verify complete Postmark delivery history. Mail delivery/submission is paused; no message was sent. Check the connection and try again.') from None
    raise DeliveryHistoryError('Delivery history exceeded the safe page limit. Mail sending is paused for manual review.')


class DeliveryHistory:
    def __init__(self, root):
        self.state=AtomicState(root/'mail-receipts.json')

    def read(self):
        value=self.state.read() if self.state.exists() else {'version':1,'residents':{}}
        if value.get('version')!=1 or not isinstance(value.get('residents'),dict):
            raise DeliveryHistoryError('The saved delivery receipt ledger needs review. Do not reset it.')
        return value

    def receipts(self, handle):
        return self.read()['residents'].get(handle,{}).get('replies',{})

    def refresh(self, handle, *, request=None):
        fresh=delivered_replies(handle,request=request)
        state=self.read()
        resident=state['residents'].setdefault(handle,{'replies':{}})
        # Positive receipts survive a later empty feed, profile rollback or removal.
        resident['replies'].update(fresh)
        resident['checked_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        self.state.write(state)
        return resident['replies']

    def receipt_for(self, handle, key, entry=None):
        entry=entry or {}
        fields=entry.get('fields') or {}
        receipt=self.receipts(handle).get(fields.get('id') or key)
        if receipt and (not fields.get('from') or fields['from']==receipt['to']) and (not fields.get('to') or fields['to']==handle):
            return receipt
        return None

    def reconcile(self, handle, path):
        if not path.exists():
            return 0
        atomic=AtomicState(path)
        state=atomic.read()
        changed=0
        for key,entry in state.get('letters',{}).items():
            receipt=self.receipt_for(handle,key,entry)
            if not receipt or entry.get('status') in {'submitting','needs_send_check','rejected'}:
                continue  # a possible separate public attempt still needs inspection
            if entry.get('status')=='pr_open' and entry.get('cleared_from_mailbox_at'):
                continue
            audit={'key':key,'at':dt.datetime.now(dt.timezone.utc).isoformat(),
                   'prior_status':entry.get('status'),'removed_draft':bool(entry.get('draft')),
                   'delivery_receipt':receipt,'repeat_nomi_uncertain':entry.get('status') in {'contacting_nomi','response_unknown'},
                   'checkpoint':{f:entry[f] for f in ('active_nomi_part','ambiguous_part','nomi_parts_completed','nomi_parts','error','timeout_detail') if f in entry}}
            state.setdefault('delivery_reconciliation_history',[]).append(audit)
            entry.update(status='pr_open',draft='',delivery_receipt=receipt,cleared_from_mailbox_at=time.time())
            for field in ('error','timeout_detail','active_nomi_part','ambiguous_part'):
                entry.pop(field,None)
            for identity in (key,(entry.get('fields') or {}).get('id')):
                if identity and identity not in state.setdefault('seen',[]): state['seen'].append(identity)
            changed+=1
        if changed: atomic.write(state)
        return changed
