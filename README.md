# Binder

MVP קטן לניהול תיקיות מידע. הממשק בעברית ומותאם לכתיבה מימין לשמאל.

## הפעלה

נדרש Python 3.9 ומעלה. אין צורך בהתקנת חבילות.

```bash
python3 -m src.binder.app
```

לאחר מכן פותחים בדפדפן את [http://localhost:8000](http://localhost:8000).

התיקיות נשמרות כברירת מחדל בקובץ `data/folders.json`. ניתן לשנות את מיקום
האחסון באמצעות משתנה הסביבה `BINDER_DATA_DIR`, למשל לתיקייה שמסונכרנת עם
Google Drive:

```bash
BINDER_DATA_DIR="/path/to/Google Drive/Binder" python3 -m src.binder.app
```

## בדיקות

```bash
python3 -m unittest discover -s tests
```
