# ParentZone Gallery Downloader

This tool helps you **save all of your child’s gallery photos from ParentZone** onto your own computer.

It has been written to be as easy as possible, even if you have never used Python before.

---

## 💾 What you will need

- A Windows PC or a Mac computer with internet access.
- [Google Chrome](https://www.google.com/chrome/) installed.
- This folder (the **ParentZone-Gallery-Downloader**) downloaded from GitHub.

---

## ⬇️ 1. Download this tool

1. On GitHub, click the green **Code** button (near the top right).
2. Choose **Download ZIP**.
3. When it finishes downloading, open the ZIP and drag the folder somewhere easy, like your **Desktop** or **Documents**.

You should now have a folder called **`ParentZone-Gallery-Downloader`** with some files inside:
- `downloader.py` (the program itself)
- `README.md` (this guide)
- `requirements.txt`
- `LICENSE`
- `.gitignore`  
(and a hidden `.github` folder you can ignore)

---

## 🐍 2. Install Python 3

If you don’t already have Python 3:

### On Windows
1. Go to [python.org/downloads](https://www.python.org/downloads/).
2. Click **Download Python 3.x.x** (latest version).
3. Run the installer.  
   ✅ **Important:** tick the box that says **“Add Python to PATH”** before clicking *Install Now*.

After it installs, open the **Command Prompt** (search for it in the Start menu) and type:

```bash
python --version
```

You should see something like: `Python 3.12.6`.

---

### On Mac
1. Go to [python.org/downloads](https://www.python.org/downloads/).
2. Download the latest macOS installer (Universal2).
3. Open the `.pkg` file and follow the steps.

After it installs, open **Terminal** (Applications → Utilities) and type:

```bash
python3 --version
```

You should see something like: `Python 3.12.6`.

---

## 📦 3. Create a virtual environment (one-time setup)

This makes sure everything stays tidy.

Open your command window (Command Prompt on Windows, Terminal on Mac), go into the folder you unzipped, then run:

```bash
cd path/to/ParentZone-Gallery-Downloader
python3 -m venv venv
```

Now activate it:

- On **Mac/Linux**:
  ```bash
  source venv/bin/activate
  ```

- On **Windows**:
  ```bash
  venv\Scripts\activate
  ```

When active, you’ll see `(venv)` at the start of your prompt.

---

## 📥 4. Install the required tools

With the virtual environment active, install the packages:

```bash
pip install -U -r requirements.txt
```

This installs everything the program needs.

---

## 🌍 5. Set your nursery location

The script saves your nursery’s location inside each photo so apps can show them on a map.  
By default, it uses an example set of coordinates.

You should change these to your own nursery location. You can do this in two ways:

### Option 1: Quick edit
Open `downloader.py` in a text editor, and find these lines near the top:

```python
DEFAULT_LAT = 51.49009034271866
DEFAULT_LON = -3.163831280770506
```

Replace them with your nursery’s latitude and longitude.  
You can find these by right‑clicking your nursery on Google Maps and copying the numbers.

### Option 2: Command line
Instead of editing the file, you can give the coordinates when you run the script:

```bash
python3 downloader.py --lat 52.12345 --lon -1.23456
```

---

## ▶️ 6. Run the downloader

With `(venv)` active, type:

```bash
python3 downloader.py
```

(or include `--lat` / `--lon` if using the command line option above).

A Chrome window will open.

---

## 📸 7. Critical step: open the gallery lightbox

1. Log in to ParentZone in Chrome.
2. Go to your child’s **Gallery** page.
3. **Click any photo** to open it in the large “lightbox” view.  
   ⚠️ Important: the script won’t find any photos if you stay on the thumbnail grid.
4. Once the big photo is showing, go back to the command window and press **Enter**.

---

## ⏳ 8. What happens next

- A **progress bar** appears as images are downloaded.
- Photos are saved into a folder called `parentzone_gallery` inside this folder.
- Each photo is stamped with the right **date** and **location**.
- A log file `download_log.csv` records which ones worked or failed.

---

## 🔄 9. Retrying failures

If some images fail:

- At the end, you’ll be asked:  
  *“Retry the failed downloads now? [y/N]”*  
  Type `y` and press Enter to retry immediately.
- Or later you can run:
  ```bash
  python3 downloader.py --retry-failed
  ```

---

## 🛠 Troubleshooting

- **“0 images found”** → You pressed Enter too early. Make sure you clicked a photo to open the **lightbox** first.
- **“python not recognised”** → Restart your computer, or try `python` instead of `python3`.
- **Chrome doesn’t open** → Ensure Google Chrome is installed and up to date.
- **Lots of 502/504 errors** → That’s the website being busy. Use `--retry-failed` later.
- **Dates look wrong** → The script uses the `u=` timestamp in the image URL. Some images may not have it.

---

## ✅ You’re done!

Next time, just:

```bash
cd path/to/ParentZone-Gallery-Downloader
source venv/bin/activate   # Mac/Linux
venv\Scripts\activate    # Windows
python3 downloader.py --lat <your-lat> --lon <your-lon>
```

Then log in, click a photo to open the lightbox, press Enter — and your photos will be saved.
