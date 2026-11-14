#!/usr/bin/env python3
"""
Email yourself the next batch of Instagram accounts that don't follow you back.

Each run:
  1. Works out who doesn't follow you back from the export files in ./data
  2. Skips anyone already emailed (tracked in state.json) or listed in keep.txt
  3. Emails you the next 50 as a clean, tappable checklist
  4. Records them as sent, so the next run continues where this one stopped

Other modes:
  --dry-run      write preview.html, send nothing, change nothing
  --resend N     email batch N again (e.g. you didn't finish it); progress is untouched

Environment variables (GitHub Actions secrets):
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, and optionally EMAIL_TO (defaults to GMAIL_ADDRESS)
"""
import argparse
import json
import math
import os
import smtplib
import sys
from email.message import EmailMessage
from html import escape
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
KEEP_FILE = HERE / "keep.txt"
DATA_DIR = HERE / "data"
BATCH_SIZE = 50
GROUP_SIZE = 10


# ----------------------------------------------------------------- data ----
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_non_followers(follower_files, following_file):
    followers = set()
    for path in follower_files:
        for item in load_json(path):
            for entry in item.get("string_list_data", []):
                if entry.get("value"):
                    followers.add(entry["value"].strip().lower())

    data = load_json(following_file)
    items = data.get("relationships_following", []) if isinstance(data, dict) else data
    following = {}
    for item in items:
        name = item.get("title") or next(
            (e.get("value") for e in item.get("string_list_data", []) if e.get("value")), None
        )
        if name:
            following[name.strip().lower()] = name.strip()

    return sorted(following[u] for u in following if u not in followers)


def load_state():
    state = load_json(STATE_FILE) if STATE_FILE.exists() else {}
    state.setdefault("sent", [])
    state.setdefault("batches", 0)
    state.setdefault("history", {})
    state.setdefault("done_email_sent", False)
    return state


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


def load_keep():
    if not KEEP_FILE.exists():
        return set()
    return {
        line.strip().lstrip("@").lower()
        for line in KEEP_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


# ---------------------------------------------------------------- email ----
def profile_url(name):
    return f"https://www.instagram.com/{name}/"


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield i, seq[i:i + size]


def build_html(batch, batch_no, total_batches, sent_before, total, resend=False):
    n = len(batch)
    sent_after = sent_before + n
    left = max(total - sent_after, 0)
    pct = 100 if total == 0 else max(1, round(100 * sent_after / total))

    groups = []
    for start, group in chunks(batch, GROUP_SIZE):
        rows = []
        for j, name in enumerate(group):
            num = start + j + 1
            bg = "#ffffff" if j % 2 == 0 else "#f6f7f9"
            url = escape(profile_url(name))
            rows.append(
                f'<tr style="background:{bg}">'
                f'<td width="44" align="center" style="padding:12px 0 12px 10px;'
                f'font:700 15px Arial,Helvetica,sans-serif;color:#6b7280">{num}</td>'
                f'<td style="padding:12px 8px;font:600 16px Arial,Helvetica,sans-serif;'
                f'word-break:break-all"><a href="{url}" style="color:#111827;'
                f'text-decoration:none">{escape(name)}</a></td>'
                f'<td width="80" align="right" style="padding:8px 10px 8px 0">'
                f'<a href="{url}" style="display:inline-block;background:#2563eb;color:#ffffff;'
                f'text-decoration:none;padding:9px 16px;border-radius:6px;'
                f'font:700 14px Arial,Helvetica,sans-serif">Open</a></td></tr>'
            )
        last = start + len(group)
        groups.append(
            f'<tr><td style="padding:18px 0 6px 0;font:700 13px Arial,Helvetica,sans-serif;'
            f'letter-spacing:.5px;color:#374151;text-transform:uppercase">'
            f'Accounts {start + 1}&ndash;{last}</td></tr>'
            f'<tr><td><table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
            f'style="border:1px solid #e5e7eb;border-radius:8px;border-collapse:separate;'
            f'overflow:hidden">{"".join(rows)}</table></td></tr>'
        )

    title = f"Batch {batch_no} of {total_batches}" + (" (resent)" if resend else "")
    preheader = f"{n} accounts to review today. {left} left after this batch."

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:#eef0f3">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}</div>
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#eef0f3">
<tr><td align="center" style="padding:16px 8px">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:600px">

<tr><td style="background:#ffffff;border-radius:10px;padding:20px 18px">
  <div style="font:800 22px Arial,Helvetica,sans-serif;color:#111827">{escape(title)}</div>
  <div style="font:15px Arial,Helvetica,sans-serif;color:#4b5563;padding-top:4px">
    {n} accounts today &middot; <b>{left}</b> left after this batch</div>

  <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
         style="margin-top:14px;background:#e5e7eb;border-radius:6px">
    <tr><td width="{pct}%" style="background:#16a34a;height:10px;border-radius:6px;
        font-size:0;line-height:0">&nbsp;</td><td style="font-size:0">&nbsp;</td></tr>
  </table>
  <div style="font:13px Arial,Helvetica,sans-serif;color:#6b7280;padding-top:6px">
    {sent_after} of {total} done after this batch ({pct}%)</div>
</td></tr>

<tr><td style="height:12px;font-size:0">&nbsp;</td></tr>

<tr><td style="background:#fff8e1;border:1px solid #f3dd8a;border-radius:10px;padding:14px 18px;
    font:14px/1.5 Arial,Helvetica,sans-serif;color:#5b4a00">
  <b>How to work through this</b><br>
  1. Tap <b>Open</b> on a name &rarr; tap <b>Following</b> &rarr; tap <b>Unfollow</b>.<br>
  2. Go top to bottom. The number of the last row you did is where to resume.<br>
  3. If it says <i>&ldquo;Sorry, this page isn&rsquo;t available&rdquo;</i>, skip it. The account is gone.<br>
  4. Want to keep someone? Just don&rsquo;t unfollow, and add the name to <code>keep.txt</code>.
</td></tr>

<tr><td>
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
  {"".join(groups)}
  </table>
</td></tr>

<tr><td style="padding:20px 6px;font:13px/1.5 Arial,Helvetica,sans-serif;color:#6b7280;text-align:center">
  {"That was the last batch. You are done." if left == 0 else
   "Didn&rsquo;t finish? Tomorrow&rsquo;s email will contain new names. To get this batch again, run the workflow with the batch number "
   + str(batch_no) + "."}
</td></tr>

</table></td></tr></table></body></html>"""


def build_text(batch, batch_no, total_batches, sent_before, total, resend=False):
    n = len(batch)
    sent_after = sent_before + n
    left = max(total - sent_after, 0)
    lines = [
        f"Batch {batch_no} of {total_batches}" + (" (resent)" if resend else ""),
        f"{n} accounts today, {left} left after this batch ({sent_after} of {total} done)",
        "",
        "Tap a link -> Following -> Unfollow. If the page isn't available, skip it.",
    ]
    for start, group in chunks(batch, GROUP_SIZE):
        lines += ["", f"--- Accounts {start + 1}-{start + len(group)} ---"]
        for j, name in enumerate(group):
            lines.append(f"{start + j + 1}. {name}  {profile_url(name)}")
    return "\n".join(lines)


def build_message(batch, batch_no, total_batches, sent_before, total, sender, to, resend=False):
    left = max(total - sent_before - len(batch), 0)
    prefix = "Resend: " if resend else ""
    msg = EmailMessage()
    msg["Subject"] = f"{prefix}Unfollow batch {batch_no}/{total_batches} - {len(batch)} accounts, {left} left"
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(build_text(batch, batch_no, total_batches, sent_before, total, resend))
    msg.add_alternative(
        build_html(batch, batch_no, total_batches, sent_before, total, resend), subtype="html"
    )
    return msg


def build_done_message(sender, to, total):
    msg = EmailMessage()
    msg["Subject"] = "Unfollow list complete"
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(
        f"All {total} accounts from your export have been emailed. Nothing left to send.\n"
        "To look for new non-followers, download a fresh Instagram export, replace the JSON "
        "files in data/, and the next run will pick up anyone new."
    )
    return msg


def send(msg, sender, password):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


# ----------------------------------------------------------------- main ----
def main():
    p = argparse.ArgumentParser(description="Email the next batch of non-followers.")
    p.add_argument("--data-dir", default=str(DATA_DIR))
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--dry-run", action="store_true", help="write preview.html, send nothing")
    p.add_argument("--resend", type=int, metavar="N", help="email batch N again")
    args = p.parse_args()

    data_dir = Path(args.data_dir)
    follower_files = sorted(data_dir.glob("followers*.json"))
    following_file = data_dir / "following.json"
    if not follower_files or not following_file.exists():
        sys.exit(f"Put followers_1.json (and any followers_N.json) and following.json in {data_dir}")

    state = load_state()
    already_sent = {u.lower() for u in state["sent"]}
    keep = load_keep()
    pending = [
        u for u in get_non_followers(follower_files, following_file)
        if u.lower() not in already_sent and u.lower() not in keep
    ]

    sender = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = os.environ.get("EMAIL_TO") or sender
    total = len(state["sent"]) + len(pending)
    total_batches = max(math.ceil(total / args.batch_size), state["batches"], 1)

    def need_credentials():
        if not (sender and password):
            sys.exit("GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not set.")

    # ---- resend an earlier batch (progress untouched) ----
    if args.resend:
        key = str(args.resend)
        if key not in state["history"]:
            sys.exit(f"Batch {key} not found. Sent batches: 1-{state['batches']}")
        batch = state["history"][key]
        sent_before = sum(len(state["history"].get(str(k), [])) for k in range(1, args.resend))
        msg = build_message(batch, args.resend, total_batches, sent_before, total,
                            sender or "me", to or "me", resend=True)
        if args.dry_run:
            Path("preview.html").write_text(msg.get_body(("html",)).get_content(), encoding="utf-8")
            print(f"Dry run: resend of batch {key} written to preview.html")
            return
        need_credentials()
        try:
            send(msg, sender, password)
            print(f"Resent batch {key}.")
        except Exception as e:
            print(f"Error sending the email: {e}")
        return

    # ---- nothing left ----
    if not pending:
        if state["done_email_sent"]:
            print("Everything has already been sent. Nothing to do.")
            return
        print("No accounts left.")
        if not args.dry_run:
            need_credentials()
            send(build_done_message(sender, to, len(state["sent"])), sender, password)
            state["done_email_sent"] = True
            save_state(state)
        return

    # ---- normal run ----
    batch = pending[: args.batch_size]
    batch_no = state["batches"] + 1
    sent_before = len(state["sent"])
    total_batches = max(total_batches, batch_no)
    msg = build_message(batch, batch_no, total_batches, sent_before, total,
                        sender or "me", to or "me")

    if args.dry_run:
        Path("preview.html").write_text(msg.get_body(("html",)).get_content(), encoding="utf-8")
        print(f"Dry run: batch {batch_no}/{total_batches}, {len(batch)} accounts, "
              f"{total - sent_before - len(batch)} would remain. Written to preview.html")
        return

    need_credentials()
    send(msg, sender, password)  # progress is only saved if sending succeeded
    state["sent"].extend(batch)
    state["batches"] = batch_no
    state["history"][str(batch_no)] = batch
    state["done_email_sent"] = False
    save_state(state)
    print(f"Sent batch {batch_no}/{total_batches} ({len(batch)} accounts).")


if __name__ == "__main__":
    main()
