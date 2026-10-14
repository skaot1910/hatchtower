"""E-Mails: einheitliches Layout (HTML mit Text-Fassung), Logo, Sprache und eine Mail pro Empfänger."""
import asyncio
import io

from PIL import Image

from hatchtower import db, i18n, mailer, main, notify, security


def _cfg():
    return mailer.MailConfig(enabled=True, host="smtp.example.com", port=587, security="starttls", username="u",
                             password="p", sender="hatchtower@example.com", sender_name="Hatchtower",
                             base_url="https://ht.example.com")


def _mail(**kw):
    return mailer.Mail(subject="Betreff", heading="Überschrift", name="olga", paragraphs=["Erster <Absatz>"],
                       button=("Los", "https://ht.example.com/x?a=1&b=2"), facts=[("Konto", "olga")],
                       note="Hinweis", **kw)


def _parts(msg):
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
    return text, html, images


def test_html_und_text_mit_anrede_knopf_und_angaben(world):
    db.set_setting("brand_logo_header", "", None)
    text, html, images = _parts(mailer.build_message(_cfg(), ["a@example.com"], _mail()))
    assert "Hallo olga," in text and "Los:\nhttps://ht.example.com/x?a=1&b=2" in text and "Viele Grüße" in text
    assert "Hallo olga," in html and "Erster &lt;Absatz&gt;" in html          # Inhalte werden maskiert
    assert 'href="https://ht.example.com/x?a=1&amp;b=2"' in html and "Konto" in html
    assert "https://ht.example.com" in text                                    # Fußzeile mit Adresse
    assert images == []


def test_logo_wird_als_png_eingebettet(world, tmp_path, monkeypatch):
    out = io.BytesIO()
    Image.new("RGBA", (400, 200), (109, 40, 217, 255)).save(out, "WEBP")
    (tmp_path / "logo.webp").write_bytes(out.getvalue())
    monkeypatch.setattr(mailer, "LOGO_DIR", tmp_path)
    db.set_setting("brand_logo_header", "logo.webp", None)
    msg = mailer.build_message(_cfg(), ["a@example.com"], _mail())
    _text, html, images = _parts(msg)
    assert len(images) == 1 and images[0].get_content_type() == "image/png"
    cid = images[0]["Content-ID"].strip("<>")
    assert f'src="cid:{cid}"' in html
    with Image.open(io.BytesIO(images[0].get_content())) as img:
        assert img.height <= 96


def test_kaputtes_logo_gibt_mail_ohne_logo(world, tmp_path, monkeypatch):
    (tmp_path / "kaputt.webp").write_bytes(b"RIFF")
    monkeypatch.setattr(mailer, "LOGO_DIR", tmp_path)
    db.set_setting("brand_logo_header", "kaputt.webp", None)
    _text, html, images = _parts(mailer.build_message(_cfg(), ["a@example.com"], _mail()))
    assert images == [] and "Hatchtower" in html


def test_eigener_name_und_sprache_der_mail(world):
    db.set_setting("brand_name", "Clan-Server", None)
    i18n.set_language("en")
    mail = _mail()
    i18n.set_language("de")                       # beim Versand gilt die Sprache vom Erstellen
    text, html, _ = _parts(mailer.build_message(_cfg(), ["a@example.com"], mail))
    assert "Hello olga," in text and "Clan-Server" in html and "Hatchtower" in text   # „Bereitgestellt mit“


def test_benachrichtigung_geht_einzeln_an_jeden_empfaenger(world, monkeypatch):
    for key, value in {"mail_enabled": "1", "mail_host": "smtp.example.com", "mail_from": "h@example.com",
                       "mail_base_url": "https://ht.example.com"}.items():
        db.set_setting(key, value, None)
    with db.connect() as con:
        con.execute("UPDATE users SET email = username || '@example.com', email_verified = 1, notify_email = 1"
                    " WHERE role = 'owner'")
        owners = [r["username"] for r in con.execute("SELECT username FROM users WHERE role = 'owner' AND is_active = 1")]
    sent = []

    async def fake_send(cfg, to, subject, mail):
        sent.append((to, mail))
    async def no_webhook(*a, **k):
        pass
    monkeypatch.setattr(mailer, "send", fake_send)
    monkeypatch.setattr(notify, "_post_webhook", no_webhook)
    asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "Exit-Code 1"))
    assert sorted(to for to, _ in sent) == sorted(f"{o}@example.com" for o in owners)
    to, mail = sent[0]
    assert isinstance(to, str) and mail.name in owners
    assert mail.button[1] == "https://ht.example.com/servers/srv-a" and "Exit-Code 1" in mail.paragraphs


# -- Abmeldelink (DSGVO, RFC 8058) ---------------------------------------------------------------

def _abonnent(world):
    uid = world.ids["victim"]
    with db.connect() as con:
        con.execute("UPDATE users SET email = 'v@example.com', email_verified = 1, notify_email = 1 WHERE id = ?",
                    (uid,))
    return uid


def _notify_on(uid):
    with db.connect() as con:
        return con.execute("SELECT notify_email FROM users WHERE id = ?", (uid,)).fetchone()[0]


def test_benachrichtigung_hat_abmeldelink_und_header(world, monkeypatch):
    uid = _abonnent(world)
    for key, value in {"mail_enabled": "1", "mail_host": "smtp.example.com", "mail_from": "h@example.com",
                       "mail_base_url": "https://ht.example.com"}.items():
        db.set_setting(key, value, None)
    world.grant(uid, {"srv-a": {"server.view"}})
    sent = []

    async def fake_send(cfg, to, subject, mail):
        sent.append((to, mail))

    async def no_webhook(*a, **k):
        pass
    monkeypatch.setattr(mailer, "send", fake_send)
    monkeypatch.setattr(notify, "_post_webhook", no_webhook)
    asyncio.run(notify.deliver(main.vault, "crash", "srv-a", "Exit-Code 1"))
    mail = next(m for to, m in sent if to == "v@example.com")
    assert mail.unsubscribe.startswith("https://ht.example.com/notify/unsubscribe/")
    msg = mailer.build_message(_cfg(), ["v@example.com"], mail)
    assert msg["List-Unsubscribe"] == f"<{mail.unsubscribe}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "abbestellen" in msg.get_body(("html",)).get_content()


def test_abmelden_per_link_ohne_anmeldung(world):
    uid = _abonnent(world)
    path = f"/notify/unsubscribe/{security.unsubscribe_token(main.settings.secret_key, uid, 'v@example.com')}"
    c = world.client()
    page = c.get(path)
    assert page.status_code == 200 and "v@example.com" in page.text and _notify_on(uid) == 1   # GET ändert nichts
    r = c.post(path, data={"List-Unsubscribe": "One-Click"})             # wie ein Mailprogramm, ohne Cookie
    assert r.status_code == 200 and _notify_on(uid) == 0
    assert world.audit("notify.unsubscribe")


def test_abmeldelink_gilt_nach_adresswechsel_nicht_mehr(world):
    uid = _abonnent(world)
    path = f"/notify/unsubscribe/{security.unsubscribe_token(main.settings.secret_key, uid, 'v@example.com')}"
    with db.connect() as con:
        con.execute("UPDATE users SET email = 'neu@example.com' WHERE id = ?", (uid,))
    c = world.client()
    assert c.get(path).status_code == 410
    assert c.post(path).status_code == 303 and _notify_on(uid) == 1
