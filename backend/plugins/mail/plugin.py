"""mail プラグイン — Gmail SMTP 経由のメール通知。

単体では何もせず、他プラグイン（watchdog等）から呼び出される。
プラグイン間連携は PluginManager.get("mail") 経由で行う。
"""

import logging
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")


class MailPlugin(PluginBase):
    name = "mail"
    hooks = []      # hook不要。ユーティリティプラグイン
    priority = 100
    critical = False

    async def run(self, hook: str, data, ctx):
        return None

    async def initialize(self):
        """起動時に必須環境変数の存在を確認する。"""
        missing = []
        for var in ("GMAIL_USER", "GMAIL_APP_PASSWORD"):
            if not os.environ.get(var):
                missing.append(var)
        if missing:
            logger.warning(
                "mail: env vars not set: %s — email notifications will not work",
                ", ".join(missing),
            )

    def send(self, body: str, subject: str | None = None) -> bool:
        """メールを送信する。

        Returns:
            True=送信成功, False=失敗
        """
        try:
            user = os.environ["GMAIL_USER"]
            password = os.environ["GMAIL_APP_PASSWORD"]
        except KeyError as e:
            logger.error("mail: env var %s not set", e)
            return False

        from_addr = os.environ.get(
            "NOTIFY_FROM",
            f"aoi <{user.rsplit('@', 1)[0]}+aoi0707@{user.rsplit('@', 1)[1]}>",
        )
        to_addr = os.environ.get("NOTIFY_TO", user)
        tag = os.environ.get("NOTIFY_SUBJECT_TAG", "[AOI]")

        # 表示名とアドレスをパース
        if "<" in from_addr and ">" in from_addr:
            display_name = from_addr[:from_addr.index("<")].strip()
            addr = from_addr[from_addr.index("<") + 1:from_addr.index(">")]
        else:
            display_name = ""
            addr = from_addr

        # 件名
        if subject:
            full_subject = f"{tag} {subject}"
        else:
            subject_text = body.replace("\n", " ")[:40]
            full_subject = f"{tag} {subject_text}"

        # 免責フッター
        footer = (
            "\n\n---\n"
            "※このメールはフィクションのロールプレイ用自動通知です。\n"
            "* This is an automated notification for fictional roleplay purposes."
        )
        body_with_footer = body + footer

        msg = MIMEText(body_with_footer, "plain", "utf-8")
        msg["Subject"] = Header(full_subject, "utf-8")
        msg["From"] = (
            formataddr((Header(display_name, "utf-8").encode(), addr))
            if display_name else addr
        )
        msg["To"] = to_addr
        msg["Reply-To"] = addr
        msg["X-Auto-Response-Suppress"] = "All"

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(user, password)
                s.sendmail(addr, [to_addr], msg.as_string())
            logger.info("mail: sent (%s)", full_subject)
            return True
        except Exception as e:
            logger.error("mail: send failed (%s)", e)
            return False
