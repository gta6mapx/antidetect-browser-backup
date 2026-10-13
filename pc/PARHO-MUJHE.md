# PARHO MUJHE — Anti-Detect Browser (Windows Guide)

## Pehli dafa (one-time setup)

**PowerShell** kholo (Start menu → "PowerShell") aur ye **ek command** paste karke Enter dabao:

```powershell
Invoke-WebRequest -Uri "https://github.com/protectvibe14/antidetect-browser/archive/refs/heads/main.zip" -OutFile "$env:USERPROFILE\antidetect.zip"; Expand-Archive -Path "$env:USERPROFILE\antidetect.zip" -DestinationPath "$env:USERPROFILE" -Force; cd "$env:USERPROFILE\antidetect-browser-main\pc"; .\setup.bat
```

Ye karega:
1. Project download
2. Python check/install
3. Saari libraries install
4. **Camoufox browser** download (~1GB)
5. **Chromium** download (Patchright engine ke liye)

Time: internet speed pe depend, 10–30 min lag sakte hain pehli dafa.

## Roz chalana

`antidetect-browser-main\pc\run.bat` pe **double-click** karo.
Browser mein kholo: **http://127.0.0.1:8765**

Pehli dafa ek **admin login** banega — username/password black window mein print hoga. Usay kahin likh lo.

## Update karna (1 click!)

Jab bhi main naya code dun: `pc\update.bat` pe **double-click** karo.
Khud download karega, khud apply karega. Tumhara data (profiles, logins, settings) **mehfooz** rahega — sirf code update hoga.

## Testing checklist

1. **Naya profile banao** → Launch → Gmail login karo → browser band karo → dobara Launch → login **barkarar** hona chahiye
2. **2–3 profiles** alag alag banao, har ek mein alag site kholo
3. **Pixelscan / CreepJS / BrowserLeaks** kholo — score check karo
4. **Proxy lagao** (real residential) → IP location vs timezone vs language match hona chahiye
5. **Sync test:** 2 profiles select → Sync session → ek mein type karo, doosre mein mirror hona chahiye
6. **Warm-up:** kisi profile pe "Warm up" dabao

## Masla aye to

- Python version: `py --version` → 3.10+ hona chahiye
- Port busy: koi purani run.bat window band karo
- Chromium fail: Node.js install karo (https://nodejs.org), phir setup.bat dobara
- Baqi: mujhe screenshot bhejo
