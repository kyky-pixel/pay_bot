import os
import sqlite3
import gspread
from google.oauth2.service_account import Credentials
from gspread_formatting import format_cell_range, CellFormat, TextFormat

DB = "/opt/services/paybot/data/db.sqlite3"
SA = "/opt/services/paybot/secrets/google_sa.json"
SPREADSHEET_ID = os.environ.get("GSHEET_ID", "").strip()

PAYMENT_LABELS = {"cash": "Нал", "bank": "Безнал", "bizcard": "Бизнес-карта"}
BUDGET_LABELS = {
    "aho": "АХО",
    "mbp": "МБП",
    "kitchen": "Закупка кухня",
    "bar": "Закупка бар",
    "tech": "Тех часть",
    "fot": "ФОТ",
    "marketing": "Маркетинг",
    "other": "Другое",
}

HEADER = ["Дата","№","Автор","За что платим","Сумма","Оплата","Статья","Статус","Кто решил","Комментарий решения"]

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def month_sheet_title(ts: str) -> str:
    y, m, _ = ts.split(" ", 1)[0].split("-")
    return f"{int(m):02d}.{y}"

def ensure_sheet(sh, title: str):
    try:
        return sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=2000, cols=20)
        ws.append_row(HEADER)
        return ws

def strip_totals(ws):
    values = ws.get_all_values()
    if not values:
        return
    rows_to_delete = []
    for idx, row in enumerate(values, start=1):
        if len(row) >= 4 and "ИТОГО" in (row[3] or ""):
            rows_to_delete.append(idx)
    for r in reversed(rows_to_delete):
        ws.delete_rows(r)

def compute_totals(year: str, month2: str):
    with conn() as c:
        rows = c.execute("""
            SELECT status, COUNT(*) as cnt, COALESCE(SUM(amount), 0) as s
            FROM requests
            WHERE status IN ('approved','rejected')
              AND decision_at IS NOT NULL
              AND strftime('%Y', decision_at) = ?
              AND strftime('%m', decision_at) = ?
            GROUP BY status
        """, (year, month2)).fetchall()
    by = {r["status"]: (int(r["cnt"]), float(r["s"])) for r in rows}
    a_cnt, a_sum = by.get("approved", (0, 0.0))
    r_cnt, r_sum = by.get("rejected", (0, 0.0))
    return a_cnt, a_sum, r_cnt, r_sum

def append_totals(ws, year: str, month2: str):
    a_cnt, a_sum, r_cnt, r_sum = compute_totals(year, month2)
    values = ws.get_all_values()
    start_row = len(values) + 1

    ws.append_row(["","","","----- ИТОГО -----","","","","","",""])
    ws.append_row(["","","","ИТОГО согласовано", float(a_sum),"","","","", f"кол-во: {a_cnt}"])
    ws.append_row(["","","","ИТОГО отклонено",  float(r_sum),"","","","", f"кол-во: {r_cnt}"])

    end_row = start_row + 2
    bold = CellFormat(textFormat=TextFormat(bold=True))
    format_cell_range(ws, f"A{start_row}:J{end_row}", bold)

def main():
    if not SPREADSHEET_ID:
        raise SystemExit("GSHEET_ID is empty")

    creds = Credentials.from_service_account_file(
        SA,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    with conn() as c:
        row = c.execute("""
            SELECT *
            FROM requests
            WHERE status IN ('approved','rejected')
              AND exported_to_sheets = 0
            ORDER BY decision_at ASC
            LIMIT 1
        """).fetchone()

        if not row:
            print("nothing_to_export")
            return

        sheet_ts = row["decision_at"] or row["created_at"]
        ws = ensure_sheet(sh, month_sheet_title(sheet_ts))
        strip_totals(ws)

        pay = (row["payment_type"] or "bank").strip()
        bud = (row["budget_category"] or "other").strip()
        pay_label = PAYMENT_LABELS.get(pay, pay)
        bud_label = BUDGET_LABELS.get(bud, bud)

        ws.append_row([
            row["created_at"],
            row["id"],
            row["author_name"],
            row["title"],
            float(row["amount"]),
            pay_label,
            bud_label,
            row["status"],
            row["decision_by_name"] or "",
            row["decision_comment"] or "",
        ])

        c.execute("UPDATE requests SET exported_to_sheets = 1 WHERE id = ?", (row["id"],))
        c.commit()

        y, m, _ = (row["decision_at"] or row["created_at"]).split(" ", 1)[0].split("-")
        append_totals(ws, y, m)

        print(f"exported:{row['id']}")

if __name__ == "__main__":
    main()
