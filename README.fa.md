# claude-host-mcp

> سرور MCP محلی که به Claude Desktop دسترسی کنترل‌شده به ماشین میزبان واقعی می‌دهد — نه فقط محیط ایزوله (VM/سشن) خودش.

ساخته‌شده با MCP Python SDK نسخه **v2** (`MCPServer`) و انتقال stdio. با کاربر عادی اجرا می‌شود. روی **لینوکس، مک و ویندوز** کار می‌کند — ابزارها بر اساس سیستم‌عامل تطبیق داده می‌شوند (Bash/PowerShell، ps/tasklist، systemd/launchd/sc).

[English](README.md) | فارسی

## چرا این پروژه؟

تسک‌های Cowork/Code در Claude Desktop داخل سندباکس محدود اجرا می‌شوند. این سرور یک پل به بیرون است: Claude با **۱۱۴ ابزار تایپ‌شده و ۸ ریسورس (۵ ثابت و ۳ تمپلیت)** روی میزبان واقعی کار می‌کند — شل، ترمینال ماندگار، جاب پس‌زمینه، فایل، جست‌وجو، گیت، مانیتورینگ، ژورنال، پورت، داکر، پکیج، شبکه، اسنپشات، زمان، حافظه، تفکر، وب، مرورگر، گیت‌هاب، دیتابیس، نقشه، درایو، اسلک — همه با ریشه‌های محدود، موتور پالیسی، لاگ حسابرسی و گاردریل دستورهای خطرناک.

هدف طراحی: هر کاری که یک توسعه‌دهنده/ادمین لینوکس در ترمینال می‌کند، ایجنت هم بتواند بکند — معنایی، قابل مشاهده، قابل لغو، قابل حسابرسی و قابل برگشت.

## ابزارها

۱۱۴ ابزار در دوازده گروه (تأییدشده زنده با handshake استاندارد). فقط ابزارهای مخرب تأیید می‌خواهند (بخش [سیاست تأیید](#سیاست-تأیید)).

### هسته

| ابزار | توضیح |
|---|---|
| `host_identity` | نام میزبان، `os` (Linux/Darwin/Windows)، کرنل، معماری، کاربر، خانه، PID سرور. ابزار سلامت نصب. |
| `system_summary` | لینوکس: `hostname` و `uname` و `id` و `uptime` و `df` و `free`. مک: `df` و `vm_stat` و `sysctl hw.memsize`. ویندوز: `hostname` و `whoami` و `Get-ComputerInfo` و `Get-PSDrive`. |
| `run_command` | در لینوکس/مک Bash (با `/bin/bash -lc`) و در ویندوز PowerShell. ورودی‌ها: `command` و `cwd` و `timeout_seconds`. خروجی: `exit_code` و `stdout` و `stderr`. |
| `read_file` | خواندن فایل متنی داخل ریشه‌های مجاز خواندن. ورودی‌ها: `path` و `max_chars`. |
| `write_file` | نوشتن فایل متنی داخل ریشه‌های مجاز نوشتن. بدون `overwrite=true` روی فایل موجود نمی‌نویسد. |
| `list_directory` | لیست دایرکتوری با پیشوند `DIR` و `FILE` داخل ریشه‌های مجاز. ورودی‌ها: `path` و `max_entries`. |

### ترمینال ماندگار

به‌ازای هر سشن یک پروسس شل زنده که بین کال‌ها باقی می‌ماند. برای dev server و REPL و ssh — نه دستورهای یک‌باره.

| ابزار | توضیح |
|---|---|
| `terminal_create` | ساخت شل (یا اجرای `command` تعاملی). برمی‌گرداند: `session_id` و `pid` و `cwd`. |
| `terminal_read` | خروجی افزایشی از `cursor` به بعد. `cursor` جدید برمی‌گرداند. |
| `terminal_write` | فرستادن کلید/دستور به stdin سشن. |
| `terminal_resize` | ذخیره ابعاد (متادیتا؛ هنوز ioctl واقعی PTY نیست). |
| `terminal_signal` | `INT` و `TERM` و `KILL` (در یونیکس `HUP` هم). مخرب — تأیید می‌خواهد. |
| `terminal_wait` | بلاک تا regex در `pattern` دیده شود یا پروسس بمیرد یا timeout. جایگزین حلقه polling. |
| `terminal_close` | بستن سشن. مخرب — تأیید می‌خواهد. |
| `terminal_list` | سشن‌های زنده با pid و cwd و سن و حجم بافر. |

### جاب پس‌زمینه

| ابزار | توضیح |
|---|---|
| `job_start` | اجرای جدا. `timeout_seconds` اختیاری برای kill نگهبان. برمی‌گرداند: `job_id`. |
| `job_status` | وضعیت، pid، exit code، حجم بافرها. |
| `job_output` | خروجی افزایشی `stdout` و `stderr` از `cursor` به بعد. |
| `job_wait` | بلاک تا پایان یا timeout. بهتر از polling. |
| `job_cancel` | اول `TERM` بعد ۵ ثانیه `KILL`. مخرب — تأیید می‌خواهد. |
| `job_list` | همه جاب‌ها، یا فقط در حال اجرا با `running_only=true`. |

### فایل‌ها

| ابزار | توضیح |
|---|---|
| `file_stat` | نوع، حجم (`size_bytes`)، زمان تغییر، سطح دسترسی. |
| `file_search` | جست‌وجوی بازگشتی نام فایل (`*.log`). خطاهای دسترسی نادیده گرفته می‌شوند، نتیجه‌های موفق نگه داشته می‌شوند. |
| `file_grep` | جست‌وجوی بازگشتی داخل متن با regex. اول `rg`، بعد `grep` در یونیکس، در ویندوز جایگزین داخلی پایتون. خروجی به‌صورت `file:line`. |
| `file_copy` | کپی فایل یا دایرکتوری. مبدأ باید خواندنی، مقصد باید نوشتنی باشد. |
| `file_move` | جابه‌جایی یا تغییرنام. هر دو سر باید نوشتنی باشند. مخرب — تأیید می‌خواهد. |
| `file_delete` | حذف فایل، یا دایرکتوری با `recursive=true`. هرگز خود ریشه پیکربندی‌شده را حذف نمی‌کند. مخرب — تأیید می‌خواهد. |
| `edit_file` | جایگزینی رشته دقیق (`old` به `new`). با `dry_run=true` پیش‌نمایش diff. اگر چند تطابق مبهم باشد بدون `replace_all=true` رد می‌کند. |
| `apply_patch` | اعمال unified diff (اول باینری `patch`، اگر نبود fallback ساده). `dry_run` دارد. |
| `head_file` | N خط اول فایل. |
| `tail_file` | N خط آخر فایل. |
| `directory_tree` | درخت ASCII با `depth` و `max_entries`؛ دایرکتوری‌های نویز (`__pycache__` و `.git` و `.venv` و `node_modules`) مخفی. |
| `find_files_tool` | جست‌وجوی glob؛ اول `fd`، اگر نبود `find` یا pathlib. |
| `search_text_tool` | جست‌وجوی متنی (پیش‌فرض literal، با `regex=true` الگو). اول `rg`. |
| `fuzzy_find_tool` | جست‌وجوی subsequence در نام فایل با رتبه‌بندی، بدون وابستگی. |

### پروسس و سیستم

| ابزار | توضیح |
|---|---|
| `process_list` | در لینوکس/مک `ps` مرتب‌شده بر اساس CPU و در ویندوز `tasklist`. ورودی‌ها: زیررشته `filter` و `limit`. |
| `process_kill` | ارسال سیگنال به PID (در ویندوز بدون `HUP`). از PID شماره ۱ و خود سرور محافظت می‌کند. مخرب — تأیید می‌خواهد. |
| `service_status` | وضعیت سرویس کاربر: در لینوکس systemd سطح کاربر، در مک فیلتر `launchctl list`، در ویندوز `sc query`. |
| `disk_usage` | در لینوکس/مک `df -h` و در ویندوز حجم درایو؛ اگر `path` بدهید اندازه همان مسیر مجاز هم اضافه می‌شود. |
| `system_snapshot` | وضعیت یک‌جای cpu و حافظه و دیسک و load و دما و باتری و GPU و شبکه و uptime به‌صورت JSON. |
| `journal_query` | دم ژورنال کاربر با فیلتر `service` و `priority` و `since` (در مک `log show`). |

### گیت

| ابزار | توضیح |
|---|---|
| `git_status` | شاخه جاری به‌علاوه `status --short --branch`. |
| `git_log` | کامیت‌های اخیر با فرمت کوتاه تاریخ. ورودی: `count`. |
| `git_diff` | تغییرات ثبت‌نشده به‌علاوه `--stat`. با `staged=true` نسخه `--cached` را نشان می‌دهد. |
| `git_branch` | شاخه‌های محلی و ریموت (`branch -a -v`). |
| `git_commit` | اجرای `add -A` و `commit -m`. پیام خالی یا درخت تمیز را رد می‌کند. هرگز push نمی‌کند. مخرب — تأیید می‌خواهد. |
| `git_show` | نمایش کامیت با stat. فقط خواندنی. |
| `git_blame` | blame بازه خطوط یک فایل tracked. فقط خواندنی. |
| `git_tag` | `list` (خواندنی) و `create` و `delete`. |
| `git_stash` | `list` و `push` و `pop` و `drop`. موردهای pop و drop مخرب‌اند. |
| `git_checkout` | سوییچ شاخه (یا ساخت با `-b`). روی درخت کثیف رد می‌کند. |
| `git_reset` | حالت‌های `--soft` و `--mixed` و `--hard`؛ مورد `--hard` نیازمند `confirm=true`. |
| `git_revert` | ساخت کامیت معکوس یک revision. |
| `git_merge` | مرج شاخه؛ در صورت تعارض خروجی تعارض را می‌دهد. |
| `git_rebase` | ریبیس روی upstream؛ `abort` و `cont` برای تعارض‌ها. |
| `git_clean` | پیش‌فرض `dry_run=true` فقط پیش‌نمایش؛ اجرا نیازمند `confirm=true`. |
| `git_worktree_create` | ورک‌تری ایزوله زیر `.worktrees/` برای کار ایجنت. |
| `git_worktree_list` | لیست ورک‌تری‌ها. فقط خواندنی. |
| `git_worktree_remove` | حذف ورک‌تری ایجنت. |

### شبکه

| ابزار | توضیح |
|---|---|
| `http_fetch` | گرفتن `http(s)` با سقف حجم. برمی‌گرداند: `status` و `content_type` و `truncated` و `body`. |
| `network_check` | تست دسترسی TCP به‌علاوه `latency_ms`. ورودی‌ها: `host` و `port` و `timeout_seconds`. |
| `download_file` | دانلود `http(s)` داخل ریشه نوشتنی با سقف بایت. در صورت رد شدن از سقف، فایل ناقص را پاک می‌کند. |
| `dns_lookup` | تبدیل hostname به آدرس‌ها. |
| `interface_list` | اینترفیس‌ها با وضعیت و MAC. |
| `connection_list` | سوکت‌های فعال با `ss` یا `netstat`. |
| `port_list` | سوکت‌های listening با مالک (در لینوکس `/proc`، اگر نبود `ss` یا `lsof`). |
| `port_check` | اتصال TCP به `host:port` با latency. |
| `port_owner` | مالک یک پورت listening: pid و comm و cmdline و cwd. |
| `diagnose` | عیب‌یابی لایه‌ای: برای `host:port` یا `http(s)://` مسیر DNS به TCP به مالک به HTTP به منابع؛ برای `service:NAME` مسیر سرویس به پروسس به ژورنال به پورت‌ها. خروجی `failed_layers`. |

### داکر

نیازمند CLI داکر. تغییرها محدود به پروفایل developer/full هستند و تأیید می‌خواهند.

| ابزار | توضیح |
|---|---|
| `docker_ps` | کانتینرها (پیش‌فرض در حال اجرا، با `all=true` همه). |
| `docker_logs` | دم لاگ کانتینر. |
| `docker_inspect` | وضعیت، ایمیج، پورت‌ها، مانت‌ها. |
| `docker_start` و `docker_stop` و `docker_restart` و `docker_rm` | چرخه حیات (timeout توقف ۱۰ ثانیه). |
| `docker_exec` | اجرای `sh -c` داخل کانتینر. `--privileged` بلاک است. |

### پکیج‌ها

مدیر بومی خودکار تشخیص داده می‌شود (apt/dnf/pacman/zypper/apk/brew/flatpak/snap). جست‌وجو و info همه‌جا؛ تغییر روی apt/dnf/pacman/brew و فقط پروفایل developer/full.

| ابزار | توضیح |
|---|---|
| `package_search` | جست‌وجوی پکیج. |
| `package_info` | متادیتای پکیج. |
| `package_install` و `package_remove` | نصب/حذف. تأیید می‌خواهد. |
| `package_update` | رفرش ایندکس. تأیید می‌خواهد. |

### ذهن: زمان، حافظه، تفکر

بدون وابستگی جدید. معادل رسمی time و memory و sequential-thinking با ابزارهای استاندارد.

| ابزار | توضیح |
|---|---|
| `time_now` | زمان جاری در timezone از نوع IANA (پیش‌فرض local). |
| `time_convert` | تبدیل datetime از نوع ISO بین timezoneها. |
| `time_zones` | لیست zoneهای IANA با فیلتر اختیاری. |
| `memory_store` | ذخیره یک observation روی entity (گراف دانش ماندگار). |
| `memory_link` | رابطه تایپ‌دار بین دو entity. |
| `memory_recall` | یادآوری substring روی entityها و observationها و رابطه‌ها. |
| `memory_forget` | حذف observation یا کل entity. مخرب — تأیید می‌خواهد. |
| `think` | ثبت یک گام استدلال در زنجیره. |
| `think_list` | برگرداندن زنجیره تفکر. فقط خواندنی. |
| `think_clear` | پاک کردن زنجیره. مخرب — تأیید می‌خواهد. |

### داده وب: fetch و جست‌وجو و مرورگر

| ابزار | توضیح |
|---|---|
| `fetch_text` | گرفتن URL و تبدیل به متن آماده LLM (حذف boilerplate، حداکثر ۳ ریدایرکت). |
| `web_search` | جست‌وجوی duckduckgo بدون کلید یا Brave با کلید. پیش‌فرض خاموش (`HOST_MCP_WEB_SEARCH`). |
| `browser_fetch` | متن DOM با کروم headless. opt-in (`HOST_MCP_BROWSER=chrome`). |
| `browser_shot` | اسکرین‌شات PNG در ریشه نوشتنی. opt-in؛ تأیید می‌خواهد. |

### یکپارچه‌سازی: گیت‌هاب، دیتابیس، نقشه، درایو، اسلک

همه با کلید فعال می‌شوند؛ بدون کلید خطای راهنما می‌دهند و هرگز کرش نمی‌کنند. secret هرگز در audit لاگ نمی‌شود.

| ابزار | توضیح |
|---|---|
| `github_repo` | متادیتای ریپو (نیازمند `GITHUB_TOKEN`). |
| `github_issue` | لیست/گرفتن/ساخت issue. ساخت تأیید می‌خواهد. |
| `github_pr` | لیست/گرفتن/ساخت PR. ساخت تأیید می‌خواهد. |
| `db_query` | کوئری با اولویت خواندن: sqlite با stdlib و postgres با `psql`. نوشتن نیازمند `confirm=true` و پروفایل full. |
| `db_tables` | لیست جدول‌های یک DSN. |
| `redis_get` | گرفتن کلید با `redis-cli` (نیازمند `REDIS_URL`). |
| `maps_geocode` | ژئوکد مستقیم (گوگل با کلید، وگرنه nominatim). |
| `maps_directions` | مسیریابی (گوگل با کلید، وگرنه فاصله خط مستقیم). |
| `drive_list` | لیست مسیر ریموت `rclone` (نیازمند `RCLONE_REMOTE`). |
| `drive_get` | دانلود فایل ریموت در ریشه نوشتنی. تأیید می‌خواهد. |
| `slack_list` | لیست کانال‌ها (نیازمند `SLACK_BOT_TOKEN`). |
| `slack_send` | ارسال پیام. تأیید می‌خواهد. |

### اسنپشات و حسابرسی

| ابزار | توضیح |
|---|---|
| `snapshot_create` | کپی فایل/دایرکتوری در اسلات زمان‌دار قبل از عملیات پرریسک. |
| `snapshot_list` | اسلات‌ها با مبدأ و زمان ساخت. |
| `snapshot_restore` | برگرداندن اسلات. مخرب — تأیید می‌خواهد؛ روی تداخل نیازمند `overwrite`. |
| `file_version` | اسنپشات تک‌فراخوانی یک فایل قبل از ویرایش. |
| `file_restore` | برگرداندن جدیدترین اسلات ثبت‌شده برای یک مسیر. مخرب — تأیید می‌خواهد. |
| `audit_log` | آخرین رکوردهای حسابرسی (فقط مسیر و حجم، هرگز محتوا). |
| `audit_search` | فیلتر با زیررشته ابزار و ok درست/غلط. |

## ریسورس‌ها

کانتکست زنده بدون tool call:

| URI | محتوا |
|---|---|
| `system://summary` | هویت یک‌خطی، uptime، دیسک، حافظه. |
| `system://snapshot` | JSON کامل `system_snapshot`. |
| `system://ports` | جدول پورت‌های listening به‌صورت JSON. |
| `policy://current` | پروفایل، ریشه‌ها، سقف‌ها و مجموعه مخرب‌ها به‌صورت JSON. |
| `audit://recent` | ۲۰ رکورد آخر حسابرسی به‌صورت JSON. |
| `process://{pid}` | سطر ps به‌علاوه cmdline و cwd به‌صورت JSON. |
| `terminal://{session}` | دم بافر به‌علاوه وضعیت زنده‌بودن به‌صورت JSON. |
| `job://{job_id}` | وضعیت به‌علاوه دم stdout و stderr به‌صورت JSON. |

## سیاست تأیید

فقط ابزارهای مخرب تأیید می‌خواهند: `file_delete` و `file_move` و `terminal_close` و `terminal_signal` و `process_kill` و `job_cancel` و `git_commit` و `git_reset` و `git_revert` و `git_merge` و `git_rebase` و `git_checkout` و `git_clean` (اجرا) و `git_tag` (ساخت/حذف) و `git_stash` (pop/drop) و `git_worktree_*` (ساخت/حذف) و `snapshot_restore` و `file_restore` و تغییرهای `docker_*` و `package_*` و `memory_forget` و `think_clear` و ساخت `github_issue` و `github_pr` و نوشتن `db_query` و `browser_shot` و `drive_get` و `slack_send`. بقیه — شل، خواندن، جست‌وجو، مانیتورینگ، ژورنال، پورت، diagnose — بدون اصطکاک تأیید اجرا می‌شوند.

> نکته: حذف از طریق شل (`rm` یا `Remove-Item` داخل `run_command`) بلاک نیست و تأیید نمی‌خواهد. برای حذف محافظت‌شده از `file_delete` استفاده کنید.

## امنیت

سرور با کاربر عادی شما اجرا می‌شود. هر چیزی که آن کاربر بتواند بخواند یا تغییر دهد از طریق این ابزارها قابل دسترس است.

بلاک‌های سخت در `run_command`: دسترسی `sudo` و `su` و `pkexec`، خاموش/ریستارت سیستم (در ویندوز `Restart-Computer` و `Stop-Computer`)، ابزارهای دیسک (`mkfs` و `wipefs` و `fdisk` و `parted` و `diskpart` و `Format-Volume` و `Clear-Disk`)، نوشتن خام `dd of=/dev/*`، حذف بازگشتی `/` یا `$HOME` (در ویندوز حذف ریشه درایو مثل `Remove-Item C:\`)، تغییر مالکیت سراسری روی `/`، fork bomb.

موتور پالیسی (`HOST_MCP_PROFILE`): `safe` یعنی فقط ابزارهای خواندنی رد می‌شوند و بقیه در سمت سرور بلاک‌اند؛ `developer` (پیش‌فرض) یعنی فضای کاری کامل به‌علاوه گیت و پروسس و شبکه؛ `full` یعنی developer به‌علاوه عملیات سبک داکر/پکیج/ریستارت سرویس. عملیات مخرب گیت (`reset --hard` و اجرای `clean`) علاوه بر تأیید کلاینت نیازمند `confirm=true` در خود کال هستند. `docker_exec --privileged` همیشه بلاک است. `process_kill` به PID شماره ۱ و خود سرور سیگنال نمی‌فرستد؛ `file_delete` ریشه‌های پیکربندی‌شده را حذف نمی‌کند؛ `git_commit` هرگز push نمی‌کند؛ `service_status` فقط سطح کاربر است؛ `download_file` و `http_fetch` فقط `http(s)` با سقف بایت هستند.

محدودیت نرخ (`HOST_MCP_RATE_LIMIT` با پیش‌فرض `60/60`): بودجه کال به‌ازای خانواده ابزار؛ کال اضافه با خطای rate-limit رد می‌شود نه اجرا.

حسابرسی (`~/.local/share/claude-host-mcp/audit.jsonl`، با `HOST_MCP_AUDIT_FILE` عوض می‌شود، با مقدار خالی خاموش): هر ابزار تغییردهنده timestamp و tool و hint آرگومان و ok را لاگ می‌کند. محتوای فایل هرگز لاگ نمی‌شود.

> بلاک‌لیست فقط گاردریل است، نه سندباکس. دسترسی شل ذاتاً قدرتمند است. ریشه‌های `*_ROOTS` را در حداقل لازم نگه دارید.

## نیازمندی‌ها

- لینوکس، مک یا ویندوز؛ پایتون 3.10+
- Claude Desktop با پشتیبانی MCP محلی
- `uv` اختیاری است؛ نصاب‌ها در صورت نبود به `venv` و pip برمی‌گردند

## نصب

لینوکس (یا WSL):

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
chmod +x install.sh install-mac.sh doctor.sh uninstall.sh
./install.sh
```

مک:

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
chmod +x install.sh install-mac.sh doctor.sh uninstall.sh
./install-mac.sh
```

ویندوز (PowerShell — از اسکریپت‌های `.ps1` استفاده کنید، نه `.sh`):

```powershell
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

نصاب این کارها را می‌کند:

1. کپی سورس به `~/.local/share/claude-host-mcp` (در ویندوز `%USERPROFILE%\.local\share\claude-host-mcp`)
2. ساخت venv ایزوله و نصب `mcp>=2,<3`
3. پیدا کردن کانفیگ Claude — مک `~/Library/Application Support/Claude/`، ویندوز `%APPDATA%\Claude\`، لینوکس `~/.config/Claude-3p/` یا `~/.config/Claude/`
4. ثبت سرور `host-system` (اول از کانفیگ بکاپ می‌گیرد)
5. فعال‌سازی فلگ‌های local-dev در پروفایل فعال config-library نسخه 3P، اگر وجود داشت (فقط لینوکس 3P؛ در بقیه no-op است)

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
| `HOST_MCP_PROFILE` | `developer` | `safe` (فقط خواندنی) و `developer` و `full`. |
| `HOST_MCP_READ_ROOTS` | `$HOME:/etc:/var/log` (لینوکس/مک) و `$HOME` (ویندوز) | ریشه‌های مجاز خواندن (جداکننده مسیر سیستم‌عامل). |
| `HOST_MCP_WRITE_ROOTS` | `$HOME` | ریشه‌های مجاز نوشتن (جداکننده مسیر سیستم‌عامل). |
| `HOST_MCP_MAX_OUTPUT` | `50000` | سقف برش خروجی، کاراکتر. |
| `HOST_MCP_MAX_TIMEOUT` | `180` | سقف timeout دستور، ثانیه. |
| `HOST_MCP_MAX_DOWNLOAD` | `20971520` | سقف دانلود/واکشی، بایت (۲۰ مگ). |
| `HOST_MCP_AUDIT_FILE` | `~/.local/share/claude-host-mcp/audit.jsonl` | مسیر لاگ حسابرسی؛ خالی یعنی خاموش. |
| `HOST_MCP_SNAPSHOT_DIR` | `~/.local/share/claude-host-mcp/snapshots` | دایرکتوری اسلات‌های اسنپشات. |
| `HOST_MCP_RATE_LIMIT` | `60/60` | تعداد/ثانیه به‌ازای خانواده ابزار. |
| `HOST_MCP_LOG_LEVEL` | `WARNING` | سطح لاگ پایتون. |
| `HOST_MCP_MEMORY_FILE` | `~/.local/share/claude-host-mcp/memory.json` | فایل گراف دانش. |
| `HOST_MCP_WEB_SEARCH` | `off` | مقدار `duckduckgo` جست‌وجوی بدون کلید؛ `BRAVE_API_KEY` یعنی Brave. |
| `HOST_MCP_BROWSER` | `off` | مقدار `chrome` ابزارهای مرورگر headless را فعال می‌کند. |
| `GITHUB_TOKEN` / `GH_TOKEN` | _(خالی)_ | فعال‌سازی `github_*`. |
| `POSTGRES_DSN` / `REDIS_URL` | _(خالی)_ | DSN پیش‌فرض `db_*` و `redis_get`. |
| `GOOGLE_MAPS_API_KEY` | _(خالی)_ | بک‌اند گوگل `maps_*`؛ وگرنه nominatim. |
| `RCLONE_REMOTE` | _(خالی)_ | مثل `gdrive:` برای `drive_*`. |
| `SLACK_BOT_TOKEN` | _(خالی)_ | توکن `xoxb-` برای `slack_*`. |

مثال:

```json
{
  "mcpServers": {
    "host-system": {
      "command": "/home/alice/.local/share/claude-host-mcp/.venv/bin/claude-host-mcp",
      "args": [],
      "env": {
        "HOST_MCP_PROFILE": "developer",
        "HOST_MCP_READ_ROOTS": "/home/alice:/etc:/var/log",
        "HOST_MCP_WRITE_ROOTS": "/home/alice/Documents",
        "HOST_MCP_MAX_TIMEOUT": "180",
        "HOST_MCP_MAX_OUTPUT": "50000"
      }
    }
  }
}
```

## عیب‌یابی

```bash
./doctor.sh        # لینوکس / مک
```

```powershell
.\doctor.ps1       # ویندوز
```

سیستم‌عامل، پایتون، entry point داخل venv، ایمپورت MCP SDK و ثبت‌شدن کانفیگ را چک می‌کند. لاگ‌های MCP: دایرکتوری config/log مربوط به Claude؛ در نصب 3P روی لینوکس معمولاً `~/.config/Claude-3p/logs/`.

رفع رایج:

- ریستارت کامل Claude Desktop (نه فقط بستن پنجره)، بعد سشن جدید.
- اگر اسکریپت‌ها بعد از `git clone` اجرا نشدند: `chmod +x install.sh install-mac.sh doctor.sh uninstall.sh`.
- در ویندوز اگر PowerShell اسکریپت را بلاک کرد: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` بعد `.\install.ps1`.
- اگر کانفیگ اشتباهی ویرایش شد، با `CLAUDE_DESKTOP_CONFIG` (یونیکس) یا `-ClaudeConfig` (ویندوز) مسیر درست را بدهید و دوباره نصب کنید.

## حذف نصب

```bash
./uninstall.sh     # لینوکس / مک
```

```powershell
.\uninstall.ps1    # ویندوز
```

ورودی `host-system` را حذف می‌کند (اول از کانفیگ بکاپ می‌گیرد) و runtime نصب‌شده را پاک می‌کند. بعد Claude Desktop را ریستارت کنید.

## توسعه

ساختار: `src/claude_host_mcp/` (فایل‌های `server.py` و `sessions.py` و `jobs.py` و `policy.py` و `files.py` و `gitx.py` و `ops.py` و `snapshots.py` و `resources.py` و `mind.py` و `webdata.py`) و `pyproject.toml` (hatchling) و `install.sh` و `install-mac.sh` و `install.ps1` و `doctor.sh` و `doctor.ps1` و `uninstall.sh` و `uninstall.ps1`.

```python
from mcp.server import MCPServer
mcp = MCPServer("Host System")
```

قوانین: در انتقال stdio چیزی به stdout لاگ نکنید (stdout مال JSON-RPC است؛ لاگ به stderr). تست سریع:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); import claude_host_mcp.server; print('OK')"
```

## تغییرات

[CHANGELOG.md](CHANGELOG.md) را ببینید. نسخه فعلی: `0.5.0`.

## لایسنس

MIT — فایل [LICENSE](LICENSE) را ببینید.
