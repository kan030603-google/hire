Option Explicit

Dim fso, srcDir, outRoot, folder, file, word, doc
Dim stem, outDir, pdfPath, failures

If WScript.Arguments.Count <> 2 Then
  WScript.Echo "Usage: cscript render_word.vbs <docx_dir> <output_dir>"
  WScript.Quit 2
End If

Set fso = CreateObject("Scripting.FileSystemObject")
srcDir = fso.GetAbsolutePathName(WScript.Arguments(0))
outRoot = fso.GetAbsolutePathName(WScript.Arguments(1))

If Not fso.FolderExists(outRoot) Then
  fso.CreateFolder outRoot
End If

Set word = CreateObject("Word.Application")
word.Visible = False
word.DisplayAlerts = 0
failures = 0

Set folder = fso.GetFolder(srcDir)
For Each file In folder.Files
  If LCase(fso.GetExtensionName(file.Name)) = "docx" Then
    stem = fso.GetBaseName(file.Name)
    outDir = fso.BuildPath(outRoot, stem)
    If Not fso.FolderExists(outDir) Then
      fso.CreateFolder outDir
    End If
    pdfPath = fso.BuildPath(outDir, stem & ".pdf")

    On Error Resume Next
    Err.Clear
    Set doc = word.Documents.Open(file.Path, False, True, False)
    If Err.Number = 0 Then
      doc.ExportAsFixedFormat pdfPath, 17
      If Err.Number = 0 Then
        WScript.Echo "PDF" & vbTab & stem
      Else
        WScript.Echo "ERROR" & vbTab & stem & vbTab & Err.Description
        failures = failures + 1
      End If
      doc.Close False
      Set doc = Nothing
    Else
      WScript.Echo "ERROR" & vbTab & stem & vbTab & Err.Description
      failures = failures + 1
    End If
    On Error GoTo 0
  End If
Next

word.Quit
Set word = Nothing
Set fso = Nothing

If failures > 0 Then
  WScript.Quit 1
End If
