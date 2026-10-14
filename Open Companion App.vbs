Option Explicit
Dim shell, fso, folder, command, failed
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
On Error Resume Next
command = "pyw -3 " & Chr(34) & folder & "\Companion App.pyw" & Chr(34)
shell.Run command, 1, False
failed = Err.Number
If failed <> 0 Then
  Err.Clear
  command = "pythonw " & Chr(34) & folder & "\Companion App.pyw" & Chr(34)
  shell.Run command, 1, False
  If Err.Number <> 0 Then
    MsgBox "Python could not be found. Try double-clicking Companion App.pyw, or reinstall Python with its launcher enabled.", 48, "Postmark Nomi Companion"
  End If
End If
