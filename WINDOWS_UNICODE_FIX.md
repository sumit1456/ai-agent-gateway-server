# Windows Unicode/Emoji Logging Fix

## Problem
On Windows, the default PowerShell console encoding (cp1252) cannot display Unicode emoji characters (✅, 🤖, 📦, etc.), causing `UnicodeEncodeError` when the application tries to log messages with emoji.

## Error Example
```
UnicodeEncodeError: 'charmap' codec can't encode character '\u2705' in position 56: character maps to <undefined>
```

## Solutions Implemented

### 1. Logging Configuration (app/logging_config.py)
- Configured all loggers to use UTF-8 encoding
- Added Windows-specific handling with `errors='replace'` fallback
- File logs always saved with UTF-8 encoding

### 2. PowerShell Startup Script (start_server.ps1)
Use this script to start the server with proper encoding:

```powershell
.\start_server.ps1
```

This script:
- Sets `PYTHONIOENCODING=utf-8`
- Sets `PYTHONUTF8=1`
- Configures console output encoding

### 3. Manual Environment Setup
If you prefer manual setup, run these commands before starting the server:

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

Then start normally:
```powershell
uvicorn app.main:app --reload
```

### 4. Windows Terminal (Recommended)
For best Unicode support on Windows:
1. Install [Windows Terminal](https://aka.ms/terminal) from Microsoft Store
2. Set default encoding to UTF-8 in settings
3. Use a font that supports emoji (e.g., "Cascadia Code", "Segoe UI Emoji")

## How It Works

The fix works in three layers:

1. **Environment Variables**: Tell Python to use UTF-8 for all I/O
2. **Logging Handlers**: Configure handlers with explicit UTF-8 encoding
3. **Error Handling**: Use `errors='replace'` to substitute characters that can't be displayed

## Testing

After applying the fix, you should see:
- ✅ Emoji in console (if your terminal supports it)
- ✅ Emoji in `gateway.log` file (always works)
- ❌ No more `UnicodeEncodeError` crashes

## Alternative: Remove Emoji from Logs

If you prefer logs without emoji, you can edit the log messages in:
- `app/engine/llm_utils.py`
- `app/engine/tool_loop.py`
- `app/engine/nodes.py`
- `app/engine/runner.py`

Replace emoji with text equivalents:
- ✅ → [OK]
- ❌ → [ERROR]
- 🤖 → [BOT]
- 📦 → [PKG]
- etc.

## Verification

Run this test to verify encoding:
```powershell
python -c "import sys; print(f'stdout: {sys.stdout.encoding}'); print('Test: ✅ 🤖 📦')"
```

Expected output:
```
stdout: utf-8
Test: ✅ 🤖 📦
```

## Troubleshooting

### Still seeing errors?
1. Restart your PowerShell session
2. Use the `start_server.ps1` script
3. Check your Python version (3.11+ recommended)
4. Try Windows Terminal instead of cmd.exe or PowerShell

### Emoji showing as boxes (□)?
- Your terminal font doesn't support emoji
- The fix still prevents crashes; emoji just won't display
- Check `gateway.log` file - emoji will be there

### Want to disable emoji entirely?
Set environment variable:
```powershell
$env:DISABLE_EMOJI_LOGS = "1"
```

Then update logging code to check this variable.
