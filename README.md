# YTDM — ترمینال دانلود یوتیوب

یک دانلودمنجر یوتیوب با ظاهر **Hacker Terminal / Cyberpunk**، ساخته‌شده دور موتور
[yt-dlp](https://github.com/yt-dlp/yt-dlp). رابط گرافیکی یک ترمینال تعاملی کامل است
که با [Rich](https://github.com/Textualize/rich) ساخته شده و همه ابزارهای مورد نیاز
(yt-dlp، ffmpeg و deno) به‌صورت باینری در `bin/` قرار دارند.

```
╔══════════════════════════════════════════════════════════════╗
║     ██╗   ██╗████████╗    ██████╗ ██╗      ███████╗          ║
║     ╚██╗ ██╔╝╚══██╔══╝    ██╔══██╗██║      ██╔════╝          ║
║      ╚████╔╝    ██║       ██║  ██║██║      █████╗            ║
║       ╚██╔╝     ██║       ██║  ██║██║      ██╔══╝            ║
║        ██║      ██║       ██████╔╝███████╗ ███████╗          ║
║        ╚═╝      ╚═╝       ╚═════╝ ╚══════╝ ╚══════╝          ║
║                                                              ║
║                YOUTUBE DOWNLOADER · CYBER TERMINAL           ║
╚══════════════════════════════════════════════════════════════╝

┌─ ┤ SYSTEM STATUS ├──────────────────────────────────────────┐
│  ● DOWNLOADER  ············  ● ONLINE   yt-dlp 2026.08.19   │
│  ● NETWORK     ············  ● READY    SYSTEM → 127.0.0.1  │
│  ● FFMPEG      ············  ● READY    bundled             │
│  ● STORAGE     ············  ● READY    D:\idm\downloads    │
└─────────────────────────────────────────────────────────────┘

┌─ ┤ MAIN MENU ├──────────────────────────────────────────────┐
│    [1]  ▶ DOWNLOAD VIDEO      [2]  ▶ DOWNLOAD AUDIO         │
│    [3]  ▶ VIDEO INFORMATION   [4]  ▶ DOWNLOAD HISTORY       │
│    [5]  ▶ SETTINGS            [6]  ▶ UPDATE YT-DLP          │
│    [0]  ▶ EXIT                                             │
└─────────────────────────────────────────────────────────────┘
root@yt-downloader:~$ █
```

## امکانات

- **ترمینال تعاملی** با تم Hacker/Cyberpunk، لوگوی ASCII، پنل‌ها و جداول Rich
- **۵ تم رنگی**: Hacker Green، Matrix، Cyberpunk، Crimson، Arctic
- دانلود ویدئو با کیفیت دلخواه (۱۴۴p تا ۲۱۶۰p یا best)
- استخراج صدا به mp3/m4a/opus/wav/aac/flac/vorbis
- **انتخاب جریان دقیق**: جدول کیفیت/فرمت/FPS/حجم واقعی ویدئو
- نمایش اطلاعات ویدئو (عنوان، کانال، مدت، بازدید، توضیحات)
- نوار پیشرفت زنده با سرعت و زمان باقی‌مانده
- مدیریت خطاهای Hacker-style با کد خطا و بازگشت به منو
- تاریخچه دانلودها (در `data/history.json`)
- تنظیمات پایدار در `data/config.json` (مسیر، کیفیت، تم، پروکسی، ffmpeg، …)
- بهینه برای Windows Terminal / PowerShell / CMD (UTF-8، ANSI، مسیرهای `C:\`/`D:\`)
- مدیریت Ctrl+C و خروج امن

## نصب

مخزن را کلون کنید و باینری‌های مورد نیاز را دریافت کنید. باینری‌ها (yt-dlp، ffmpeg،
deno — حدود ۳۳۰ مگابایت) third-party هستند و در Git قرار ندارند؛ اسکریپت زیر آن‌ها را
از صفحه‌ی رسمی هر پروژه می‌گیرد و دقیقاً در مسیری که موتور انتظار دارد می‌گذارد:

```bash
git clone https://github.com/afshinnj/YT-DL.git
cd YT-DL
bash tools/fetch-binaries.sh          # در Git Bash / WSL / لینوکس
# یا در پاورشل:
#   pwsh -File tools/fetch-binaries.ps1
```

سپس فقط کافیست `rich` را نصب کنید:

```bash
pip install -r requirements.txt
```

> **بدون این مرحله برنامه کار نمی‌کند** — `ytdm/engine.py` این مسیرها را می‌خواند:
> `bin/yt-dlp.exe`، `bin/deno.exe` و
> `bin/ffmpeg-master-latest-win64-gpl/bin/{ffmpeg,ffprobe}.exe`.

## اتصال افزونهٔ Chrome

افزونهٔ Chrome از برنامهٔ دسکتاپ برای دریافت اطلاعات و دانلود استفاده می‌کند.
برای اجرای نسخهٔ سورس، ابتدا پیش‌نیازهای بالا و باینری‌ها را نصب کنید، سپس
`browser_integration/installer/install_host.bat` را اجرا کنید تا Native Messaging
برای کاربر فعلی ویندوز ثبت شود. در Chrome به `chrome://extensions` بروید، حالت
Developer mode را روشن کنید و با **Load unpacked** پوشهٔ `chrome_extension` را
انتخاب کنید. بعد از نصب یا به‌روزرسانی، افزونه را از همان صفحه Reload کنید.

برای نسخهٔ قابل‌حمل، فایل `install_host.bat` کنار فایل اجرایی برنامه قرار دارد؛
آن را از همان پوشه اجرا کنید تا میزبان افزونه ثبت و بررسی شود. افزونه امکان
انتخاب کیفیت ویدئو یا قالب صدا را پیش از شروع دانلود فراهم می‌کند.

## پیش‌نیازها

- پایتون ۳.۱۰ یا بالاتر (روی این سیستم ۳.۱۴ نصب است؛ از `py` استفاده کنید)
- کتابخانه `rich` — تنها وابستگی خارجی است:
  ```bash
  pip install -r requirements.txt
  ```
- اتصال اینترنت — اگر یوتیوب در شبکه شما فیلتر است، باید یک پروکسی فعال داشته باشید.
  برنامه پروکسی تنظیم‌شده در وینداز (`ProxyServer` در Internet Settings)، متغیرهای
  محیطی `HTTPS_PROXY`/`HTTP_PROXY` یا پروکسی تنظیم‌شده در بخش Settings را شناسایی می‌کند.

## نحوه اجرا

```bash
py main.py              # ترمینال تعاملی (پیشنهادی)
py main.py tui          # همان، صریح
run.bat                 # از طریق فایل اجراکننده
```

برای دانلود مستقیم از خط فرمان، همانند قبل:

```bash
py main.py https://www.youtube.com/watch?v=VIDEO_ID
py main.py URL -q 720                       # حداکثر کیفیت ۷۲۰p
py main.py URL -a --audio-format mp3        # استخراج صدا
py main.py URL -o D:\Videos                 # مسیر ذخیره
py main.py URL --playlist                   # کل پلی‌لیست
py main.py formats URL                      # لیست جریان‌ها
py main.py info URL                         # اطلاعات ویدئو
py main.py update                           # آپدیت yt-dlp
```

## ساختار پروژه

```
idm/
├── main.py                  نقطه ورود (+ تطبیق UTF-8 ویندوز)
├── run.bat                  اجراکننده سریع
├── requirements.txt         وابستگی‌ها (فقط rich)
├── bin/                     باینری‌های بسته‌بندی‌شده
│   ├── yt-dlp.exe
│   ├── deno.exe
│   └── ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
├── data/                    داده‌های برنامه (ساخته می‌شود)
│   ├── config.json          تنظیمات
│   ├── history.json         تاریخچه دانلودها
│   └── logs/app.log         لاگ
├── downloads/               محل ذخیره پیش‌فرض
├── tools/
│   ├── fetch-binaries.sh    دریافت yt-dlp/ffmpeg/deno (Git Bash, WSL, Linux)
│   └── fetch-binaries.ps1   همان، برای PowerShell
├── YT-DL-CyberTerminal.spec تنظیمات PyInstaller برای ساخت نسخه‌ی exe
└── ytdm/
    ├── engine.py            موتور دانلود (درایو yt-dlp) — دست‌نخورده
    ├── cli.py               رابط خط فرمان کلاسیک
    ├── ui.py                کمک‌کننده‌های نمایش کلاسیک
    ├── config/settings.py   تنظیمات پایدار
    ├── history/store.py     تاریخچه دانلودها
    ├── utils/               کمک‌کننده‌ها و لاگر
    └── tui/                 ترمینال تعاملی
        ├── theme.py         تم‌ها، پالت‌ها، ASCII art
        ├── interface.py     کنسول Rich و ویجت‌ها
        ├── progress.py      نوار پیشرفت زنده
        ├── screens.py       صفحه‌ها (دانلود، اطلاعات، تاریخچه، تنظیمات)
        └── menu.py          حلقه اصلی و بوت
```

## ساخت نسخه‌ی قابل‌حمل ( exe )

با PyInstaller می‌توان یک پوشه‌ی خودکفا ساخت که روی وینداز بدون پایتون اجرا می‌شود
(حدود ۴۵۶ مگابایت — شامل yt-dlp، ffmpeg و deno):

```bash
py -m pip install pyinstaller
py -m PyInstaller YT-DL-CyberTerminal.spec --noconfirm
```

خروجی در `dist/YT-DL-CyberTerminal/` قرار می‌گیرد. این پوشه را می‌توان کپی و در هر
جا اجرا کرد؛ تنظیمات و تاریخچه در `data/` کنار فایل اجرایی ذخیره می‌شوند.

## نکات

- **موتور دانلود دست‌نخورده باقی مانده.** لایه ترمینال فقط از طریق API عمومی موتور
  (`probe`، `download`، `list_formats`) با آن صحبت می‌کند و منطق دانلود تغییر نکرده است.
- **فرمت خروجی ویدئو** MP4 است (ترجیح موتور). اگر یوتیوب برای آن کیفیت فقط webm
  داشته باشد، فایل با ffmpeg به mp4 تبدیل می‌شود.
- **به‌روزرسانی:** استخراج‌کننده‌های یوتیوب مرتب تغییر می‌کنند. اگر دانلود ناگهان
  متوقف شد، از گزینه `[6] UPDATE YT-DLP` یا `py main.py update` استفاده کنید.
- **فیلترینگ:** خطای رایج `Unable to download webpage` یا رد شدن اتصال به
  `www.youtube.com:443` نشان‌دهنده نبود پروکسی فعال است.
