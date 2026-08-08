import csv
import io


def _header_text(value) -> str:
    """Вопрос может быть на одном или двух языках — в заголовке CSV
    показываем оба варианта через ' / ', чтобы не терять перевод."""
    if isinstance(value, dict):
        ru = str(value.get("ru") or "").strip()
        en = str(value.get("en") or "").strip()
        if ru and en and ru != en:
            return f"{ru} / {en}"
        return ru or en
    return str(value or "")


def build_csv_bytes(survey: dict, responses: list) -> bytes:
    """Build the same CSV export used by /survey results, reused by the web panel."""
    questions = survey["questions"]
    headers = ["Респондент"] + [_header_text(q["text"]) for q in questions] + ["Дата (UTC)"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for i, r in enumerate(responses, start=1):
        respondent = f"Аноним #{i}" if survey["anonymous"] else str(r["user_id"])
        row = [respondent]
        for q_idx in range(len(questions)):
            ans = r["answers"].get(str(q_idx), {})
            row.append(ans.get("answer", ""))
        row.append(r["submitted_at"])
        writer.writerow(row)
    buf.seek(0)
    return buf.getvalue().encode("utf-8-sig")
