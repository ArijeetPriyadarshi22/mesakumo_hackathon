[Setup]
AppId=TenderWorkbenchLocal
AppName=TenderWorkbench
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\TenderWorkbench
DefaultGroupName=TenderWorkbench
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=TenderWorkbench-Setup
Compression=lzma2
SolidCompression=yes

[Files]
Source: "..\..\mesakumo\*.py"; DestDir: "{app}\mesakumo"; Flags: recursesubdirs ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\Mesakumo_Document_Inventory.csv"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; DestName: "README-Windows.md"; Flags: ignoreversion
Source: "Launch-TenderWorkbench.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\TenderWorkbench"; Filename: "{app}\Launch-TenderWorkbench.cmd"; WorkingDir: "{app}"
