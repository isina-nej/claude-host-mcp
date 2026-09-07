# claude-host-mcp

> سرور MCP محلی که به Claude Desktop دسترسی کنترل‌شده به ماشین میزبان واقعی می‌دهد — نه فقط محیط ایزوله (VM/سشن) خودش.

ساخته‌شده با MCP Python SDK نسخه **v2** (`MCPServer`) و انتقال stdio. با کاربر عادی اجرا می‌شود. روی **لینوکس، مک و ویندوز** کار می‌کند — ابزارها بر اساس سیستم‌عامل تطبیق داده می‌شوند (Bash/PowerShell، ps/tasklist، systemd/launchd/sc).

[English](README.md) | فارسی

## چرا این پروژه؟

تسک‌های Cowork/Code در Claude Desktop داخل سندباکس محدود اجرا می‌شوند. این سرور یک پل به بیرون است: Claude از طریق ۲۴ ابزار تایپ‌شده روی میزبان واقعی دستور اجرا می‌کند، فایل می‌خواند و می‌نویسد، پروسس‌ها را می‌بیند، با گیت کار می‌کند و به شبکه وصل می‌شود — همه با ریشه‌های فایل محدودشده و گاردریل برای دستورهای خطرناک.

## ابزارها

۲۴ ابزار در پنج گروه.

### هسته

| ابزار | توضیح |
|---|---|
| `host_identity` | نام میزبان، `os` (Linux/Darwin/Windows)، کرنل، معماری، کاربر، خانه، PID سرور. ابزار سلامت نصب. |
| `system_summary` | لینوکس: `hostname` و `uname` و `id` و `uptime` و `df` و `free`. مک: `df` و `vm_stat` و `sysctl hw.memsize`. ویندوز: `hostname` و `whoami` و `Get-ComputerInfo` و `Get-PSDrive`. |
| `run_command` | در لینوکس/مک Bash (با `/bin/bash -lc`) و در ویندوز PowerShell. ورودی‌ها: `command` و `cwd` و `timeout_seconds`. خروجی: `exit_code` و `stdout` و `stderr`. |
| `read_file` | خواندن فایل متنی داخل ریشه‌های مجاز خواندن. ورودی‌ها: `path` و `max_chars`. |
| `write_file` | نوشتن فایل متنی داخل ریشه‌های مجاز نوشتن. بدون `overwrite=true` روی فایل موجود نمی‌نویسد. |
| `list_directory` | لیست دایرکتوری با پیشوند `DIR` و `FILE` داخل ریشه‌های مجاز. ورودی‌ها: `path` و `max_entries`. |

### فایل‌ها

| ابزار | توضیح |
|---|---|
| `file_stat` | نوع، حجم (`size_bytes`)، زمان تغییر، سطح دسترسی. |
| `file_search` | جست‌وجوی بازگشتی نام فایل (`*.log`). خطاهای دسترسی نادیده گرفته می‌شوند، نتیجه‌های موفق نگه داشته می‌شوند. |
| `file_grep` | جست‌وجوی بازگشتی داخل متن با regex. اول `rg`، بعد `grep` در یونیکس، در ویندوز جایگزین داخلی پایتون. خروجی به‌صورت `file:line`. |
| `file_copy` | کپی فایل یا دایرکتوری. مبدأ باید خواندنی، مقصد باید نوشتنی باشد. |
| `file_move` | جابه‌جایی یا تغییرنام. هر دو سر باید نوشتنی باشند. |
| `file_delete` | حذف فایل، یا دایرکتوری با `recursive=true`. هرگز خود ریشه پیکربندی‌شده را حذف نمی‌کند. |

### پروسس و سیستم

| ابزار | توضیح |
|---|---|
| `process_list` | در لینوکس/مک `ps` مرتب‌شده بر اساس CPU و در ویندوز `tasklist`. ورودی‌ها: زیررشته `filter` و `limit`. |
| `process_kill` | ارسال سیگنال به PID (در ویندوز بدون `HUP`). از PID شماره ۱ و خود سرور محافظت می‌کند. |
| `service_status` | وضعیت سرویس کاربر: در لینوکس systemd سطح کاربر، در مک فیلتر `launchctl list`، در ویندوز `sc query`. |
| `disk_usage` | در لینوکس/مک `df -h` و در ویندوز حجم درایو؛ اگر `path` بدهید اندازه همان مسیر مجاز هم اضافه می‌شود. |

### گیت

| ابزار | توضیح |
|---|---|
| `git_status` | شاخه جاری به‌علاوه `status --short --branch`. |
| `git_log` | کامیت‌های اخیر با فرمت کوتاه تاریخ. ورودی: `count`. |
| `git_diff` | تغییرات ثبت‌نشده به‌علاوه `--stat`. با `staged=true` نسخه `--cached` را نشان می‌دهد. |
| `git_branch` | شاخه‌های محلی و ریموت (`branch -a -v`). |
| `git_commit` | اجرای `add -A` و `commit -m`. پیام خالی یا درخت تمیز را رد می‌کند. هرگز push نمی‌کند. |

### شبکه

| ابزار | توضیح |
|---|---|
| `http_fetch` | گرفتن `http(s)` با سقف حجم. برمی‌گرداند: `status` و `content_type` و `truncated` و `body`. |
| `network_check` | تست دسترسی TCP به‌علاوه `latency_ms`. ورودی‌ها: `host` و `port` و `timeout_seconds`. |
| `download_file` | دانلود `http(s)` داخل ریشه نوشتنی با سقف بایت. در صورت رد شدن از سقف، فایل ناقص را پاک می‌کند. |

## نیازمندی‌ها

- لینوکس، مک یا ویندوز؛ پایتون 3.10+
- Claude Desktop با پشتیبانی MCP محلی
- `uv` اختیاری است؛ نصاب‌ها در صورت نبود به `venv` و pip برمی‌گردند

## نصب

لینوکس:

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
./install.sh
```

مک:

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
./install-mac.sh
```

ویندوز (PowerShell):

```powershell
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
.\install.ps1
```

نصاب این کارها را می‌کند:

1. کپی سورس به `~/.local/share/claude-host-mcp` (در ویندوز `%USERPROFILE%\.local\share\claude-host-mcp`)
2. ساخت venv ایزوله و نصب `mcp>=2,<3`
3. پیدا کردن کانفیگ Claude — مک `~/Library/Application Support/Claude/`، ویندوز `%APPDATA%\Claude\`، لینوکس `~/.config/Claude-3p/` یا `~/.config/Claude/`
4. ثبت سرور `host-system` (اول از کانفیگ بکاپ می‌گیرد)
5. فعال‌سازی فلگ‌های local-dev در پروفایل فعال config-library نسخه 3P، اگر وجود داشت (مسیر لینوکس)

بعد Claude Desktop را کامل ببندید و دوباره باز کنید و یک تسک/سشن جدید بسازید.

تست سلامت:

```text
Use the host-system MCP tool host_identity.
```

اگر نام میزبان واقعی و کاربر دسکتاپ برگشت = نصب سالم است.

مسیرهای سفارشی:

```bash
CLAUDE_DESKTOP_CONFIG="$HOME/path/claude_desktop_config.json" ./install.sh
HOST_MCP_INSTALL_DIR="$HOME/custom-dir" ./install.sh
```

## پیکربندی

زیر `host-system` و کلید `env` در `claude_desktop_config.json` تنظیم می‌شود. بعد از تغییر، Claude Desktop را ریستارت کنید.

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `HOST_MCP_READ_ROOTS` | `$HOME:/etc:/var/log` (لینوکس/مک) و `$HOME` (ویندوز) | ریشه‌های مجاز خواندن (جداکننده مسیر سیستم‌عامل). |
| `HOST_MCP_WRITE_ROOTS` | `$HOME` | ریشه‌های مجاز نوشتن (جداکننده مسیر سیستم‌عامل). |
| `HOST_MCP_MAX_OUTPUT` | `50000` | سقف برش خروجی، کاراکتر. |
| `HOST_MCP_MAX_TIMEOUT` | `180` | سقف timeout دستور، ثانیه. |
| `HOST_MCP_MAX_DOWNLOAD` | `20971520` | سقف دانلود/واکشی، بایت (۲۰ مگ). |
| `HOST_MCP_LOG_LEVEL` | `WARNING` | سطح لاگ پایتون. |

مثال:

```json
{
  "mcpServers": {
    "host-system": {
      "command": "/home/alice/.local/share/claude-host-mcp/.venv/bin/claude-host-mcp",
      "args": [],
      "env": {
        "HOST_MCP_READ_ROOTS": "/home/alice:/etc:/var/log",
        "HOST_MCP_WRITE_ROOTS": "/home/alice/Documents",
        "HOST_MCP_MAX_TIMEOUT": "180",
        "HOST_MCP_MAX_OUTPUT": "50000"
      }
    }
  }
}
```

## امنیت

سرور با کاربر عادی شما اجرا می‌شود. هر چیزی که آن کاربر بتواند بخواند یا تغییر دهد از طریق این ابزارها قابل دسترس است.

بلاک‌های سخت در `run_command`: دسترسی `sudo` و `su` و `pkexec`، خاموش/ریستارت سیستم (در ویندوز `Restart-Computer` و `Stop-Computer`)، ابزارهای دیسک (`mkfs` و `wipefs` و `fdisk` و `parted` و `diskpart` و `Format-Volume` و `Clear-Disk`)، نوشتن خام `dd of=/dev/*`، حذف بازگشتی `/` یا `$HOME` (در ویندوز حذف ریشه درایو مثل `Remove-Item C:\`)، تغییر مالکیت سراسری روی `/`، fork bomb.

محدودیت‌های اضافه: `process_kill` به PID شماره ۱ و خود سرور سیگنال نمی‌فرستد؛ `file_delete` ریشه‌های پیکربندی‌شده را حذف نمی‌کند؛ `git_commit` هرگز push نمی‌کند؛ `service_status` فقط سطح کاربر است؛ `download_file` و `http_fetch` فقط `http(s)` با سقف بایت هستند.

> بلاک‌لیست فقط گاردریل است، نه سندباکس. دسترسی شل ذاتاً قدرتمند است. تأییدیه ابزارها (approval prompts) در Claude را روشن نگه دارید. دستورها را قبل از تأیید بخوانید. ریشه‌های `*_ROOTS` را در حداقل لازم نگه دارید.

## عیب‌یابی

```bash
./doctor.sh        # لینوکس / مک
```

```powershell
.\doctor.ps1       # ویندوز
```

سیستم‌عامل، پایتون، entry point داخل venv، ایمپورت MCP SDK و ثبت‌شدن کانفیگ را چک می‌کند. لاگ‌های MCP: دایرکتوری config/log مربوط به Claude؛ در نصب 3P روی لینوکس معمولاً `~/.config/Claude-3p/logs/`.

رفع رایج: ریستارت کامل Claude Desktop (نه فقط بستن پنجره)، سشن جدید بعد از نصب، اگر umask سخت‌گیرانه است بعد از `git clone` دستور `chmod +x install.sh doctor.sh uninstall.sh`.

## حذف نصب

```bash
./uninstall.sh     # لینوکس / مک
```

```powershell
.\uninstall.ps1    # ویندوز
```

ورودی `host-system` را حذف می‌کند (اول از کانفیگ بکاپ می‌گیرد) و runtime نصب‌شده را پاک می‌کند. بعد Claude Desktop را ریستارت کنید.

## توسعه

ساختار: `src/claude_host_mcp/server.py` و `src/claude_host_mcp/__init__.py` و `pyproject.toml` (hatchling) و `install.sh` و `install-mac.sh` و `install.ps1` و `doctor.sh` و `doctor.ps1` و `uninstall.sh` و `uninstall.ps1`.

```python
from mcp.server import MCPServer
mcp = MCPServer("Host System")
```

قوانین: در انتقال stdio چیزی به stdout لاگ نکنید (stdout مال JSON-RPC است؛ لاگ به stderr). تست سریع:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); import claude_host_mcp.server; print('OK')"
```

## تغییرات

[CHANGELOG.md](CHANGELOG.md) را ببینید. نسخه فعلی: `0.3.0`.

## لایسنس

MIT — فایل [LICENSE](LICENSE) را ببینید.
