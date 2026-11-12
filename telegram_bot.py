"""Telegram bot that receives Garmin CSV exports and writes them to Excel.

IMPORTANT: Only one listener per bot token is allowed.
If n8n is also configured with this token, shut it down before starting this bot,
or create a separate bot in @BotFather for n8n testing.
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from garmin_analyzer import analyze_csv
from excel_writer import append_activities, get_summary_stats

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

# Backup path: if Excel write fails (Power BI lock), user can retry without
# re-sending the CSV to Claude by running:
#   python excel_writer.py --file data/backup_latest.json
BACKUP_PATH = Path("data/backup_latest.json")


def _is_authorized(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "שלום! אני הבוט שלך לניתוח נתוני Garmin.\n\n"
        "שלח לי קובץ CSV שהורדת מ-Garmin Connect ואני אנתח אותו ואוסיף את האימונים ל-Excel.\n\n"
        "📎 פשוט גרור את הקובץ לכאן ושלח!"
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return

    document = update.message.document
    file_name = document.file_name or ""

    if not file_name.lower().endswith(".csv"):
        await update.message.reply_text(
            "⚠️ שלח קובץ CSV בלבד (כזה שהורדת מ-Garmin Connect)."
        )
        return

    await update.message.reply_text("⏳ מנתח את הקובץ, רגע...")

    try:
        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()

        # utf-8-sig handles Windows BOM that Garmin sometimes adds
        csv_content = file_bytes.decode("utf-8-sig")

        activities = analyze_csv(csv_content)

        if not activities:
            await update.message.reply_text(
                "❌ לא נמצאו אימונים תקינים בקובץ.\n"
                "וודא שהורדת את הקובץ הנכון מ-Garmin Connect."
            )
            return

        # Save backup before attempting Excel write.
        # If Power BI has the file locked, the user can retry via CLI
        # without re-sending to Claude: python excel_writer.py --file data/backup_latest.json
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_PATH.write_text(json.dumps(activities, ensure_ascii=False), encoding="utf-8")

        new_rows = append_activities(activities)
        stats = get_summary_stats(activities)

        breakdown = stats.get("activity_breakdown", {})
        breakdown_text = "\n".join(
            f"  • {activity_type}: {count}"
            for activity_type, count in breakdown.items()
        )

        avg_hr_text = (
            f"💓 דופק ממוצע: {stats['avg_heart_rate']} bpm\n"
            if stats.get("avg_heart_rate")
            else ""
        )

        reply = (
            f"✅ הושלם! נוספו {new_rows} שורות חדשות ל-Excel.\n\n"
            f"📊 סיכום הקובץ:\n"
            f"🏃 סה\"כ אימונים: {stats.get('total_activities', 0)}\n"
            f"📍 מרחק כולל: {stats.get('total_distance_km', 0):.1f} ק\"מ\n"
            f"{avg_hr_text}"
            f"🔥 קלוריות: {stats.get('total_calories', 0):,}\n"
        )

        if breakdown_text:
            reply += f"\nסוגי אימון:\n{breakdown_text}"

        if new_rows == 0:
            reply += "\n\nℹ️ כל האימונים כבר קיימים ב-Excel (אין כפילויות)."

        await update.message.reply_text(reply)

    except PermissionError as e:
        await update.message.reply_text(
            "❌ קובץ ה-Excel נעול (כנראה פתוח ב-Power BI או Excel).\n\n"
            "1. סגור את Power BI / Excel\n"
            "2. הרץ: python excel_writer.py --file data/backup_latest.json\n\n"
            "הנתונים שמורים — אין צורך לשלוח את הקובץ מחדש."
        )
        logger.error("PermissionError writing Excel: %s", e)
    except Exception as e:
        logger.exception("Error processing CSV")
        await update.message.reply_text(
            f"❌ אירעה שגיאה בעיבוד הקובץ:\n{e}"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📎 שלח לי קובץ CSV שהורדת מ-Garmin Connect.\n\n"
        "איך להוריד:\n"
        "1. כנס ל-Garmin Connect\n"
        "2. Activities → All Activities\n"
        "3. לחץ על Export CSV\n"
        "4. שלח את הקובץ לכאן"
    )


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started (polling). Make sure n8n is NOT also polling this token.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
