"""
Интеграционные тесты без сети: FunPay-аккаунт и Telegram подменены заглушками.
Запуск: python -m unittest discover tests
"""
from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
import unittest.mock
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from FunPayAPI import types  # noqa: E402
from FunPayAPI.common.enums import Currency, OrderStatuses, SubCategoryTypes  # noqa: E402
from FunPayAPI.updater import events  # noqa: E402
from cardinal.core import Cardinal  # noqa: E402
from cardinal.storage import Storage, hash_password  # noqa: E402
from cardinal.utils import format_text  # noqa: E402

ME = 100
BUYER = 200


class FakeAccount:
    def __init__(self):
        self.id = ME
        self.username = "Seller"
        self.is_initiated = True
        self.active_sales = 1
        self.csrf_token = "x"
        self.sent: list[tuple] = []
        self.images: list[tuple] = []
        self.reviews: list[tuple] = []
        self.saved_lots: list[types.LotFields] = []
        self.deleted: list[int] = []
        self.refunds: list[str] = []
        self.raised: list[tuple] = []
        self.chats = {555: types.ChatShortcut(555, "Buyer", "привет", 0, 0, True, "")}
        cat = types.Category(1, "Dota 2")
        self.subcat = types.SubCategory(10, "Предметы", SubCategoryTypes.COMMON, cat)
        cat.add_subcategory(self.subcat)
        self.lot_fields = {
            777: {"offer_id": "777", "node_id": "10", "fields[summary][ru]": "Аркана на Pudge",
                  "fields[desc][ru]": "Описание", "price": "100", "amount": "5", "active": "on", "csrf_token": "x"},
        }

    # --- чаты
    def send_message(self, chat_id, text, chat_name=None, *a, **kw):
        self.sent.append((chat_id, text))
        return types.Message(1, text, chat_id, chat_name, BUYER, self.username, ME, "")

    def send_image(self, chat_id, image, chat_name=None, *a, **kw):
        self.images.append((chat_id, image))

    def get_chat_by_name(self, name, make_request=False):
        return next((c for c in self.chats.values() if c.name == name), None)

    def get_chat_by_id(self, chat_id, make_request=False):
        return self.chats.get(chat_id)

    def get_chats(self, update=False):
        return self.chats

    def get_chat_history(self, chat_id, interlocutor_username=None, **kw):
        return [types.Message(1, "привет", chat_id, "Buyer", BUYER, "Buyer", BUYER, "")]

    # --- заказы / отзывы
    def get_order(self, order_id):
        review = types.Review(5, "Всё супер", None, False, "", False, order_id)
        fields = {"summary": types.LotField("summary", {"ru": "Аркана на Pudge"})}
        return types.Order(order_id, OrderStatuses.PAID, self.subcat, None, None, fields, 1, 100.0, Currency.RUB,
                           None, BUYER, "Buyer", ME, self.username, 555, review, [], "ru")

    def get_sells(self, **kw):
        return None, [make_order("ABCD1234")]

    def send_review(self, order_id, text, rating=5):
        self.reviews.append((order_id, text))

    def refund(self, order_id):
        self.refunds.append(order_id)

    # --- лоты
    def get_user(self, user_id):
        profile = types.UserProfile(user_id, self.username, "", True, False, "")
        lot = types.LotShortcut(777, None, None, "Аркана на Pudge", 5, 100.0, Currency.RUB, self.subcat,
                                None, False, None, None, "")
        profile.add_lot(lot)
        return profile

    def get_my_subcategory_lots(self, subcategory_id):
        return [types.MyLotShortcut(777, None, None, "Аркана на Pudge", 5, 100.0, Currency.RUB, self.subcat,
                                    False, True, ""),
                types.MyLotShortcut(888, None, None, "Выключенный лот", 1, 50.0, Currency.RUB, self.subcat,
                                    False, False, "")]

    def get_lot_fields(self, lot_id):
        return types.LotFields(lot_id, dict(self.lot_fields[lot_id]), self.subcat)

    def save_lot(self, fields):
        fields.renew_fields()
        self.saved_lots.append(fields)
        if fields.lot_id:
            self.lot_fields[fields.lot_id] = dict(fields.fields)

    def delete_lot(self, lot_id):
        self.deleted.append(lot_id)

    def get_subcategory(self, subcategory_type, subcategory_id):
        return self.subcat if subcategory_id == self.subcat.id else None

    def method(self, request_method, api_method, headers, payload, **kw):
        if api_method.startswith("lots/offerEdit?node="):
            html = ('<form class="form-offer-editor"><input type="hidden" name="csrf_token" value="x">'
                    '<input type="hidden" name="offer_id" value="0"><input type="hidden" name="node_id" value="1142">'
                    '<input name="price" value=""><input type="checkbox" name="active" checked>'
                    '<div class="form-group"><label>Тип</label><select name="fields[type]">'
                    '<option value="a" selected>Аккаунт</option><option value="b">Буст</option></select></div></form>')
            return SimpleNamespace(content=html.encode(), status_code=200)
        raise AssertionError(f"unexpected request {api_method}")

    def get_balance(self, lot_id=0):
        return types.Balance(150.5, 100.0, 1.0, 1.0, 0.0, 0.0)

    def raise_lots(self, category_id, subcategories=None, exclude=None):
        self.raised.append((category_id, subcategories))
        if len(self.raised) > 1:
            from FunPayAPI.common.exceptions import RaiseError
            response = SimpleNamespace(status_code=200, request=SimpleNamespace(url="", headers={}, body=""))
            raise RaiseError(response, types.Category(category_id, ""), "Подождите 2 часа.", 7200)
        return None

    @property
    def subcategories(self):
        return [self.subcat]


def make_order(order_id, description="Аркана на Pudge, 2 шт.", status=OrderStatuses.PAID):
    return types.OrderShortcut(order_id, description, 200.0, Currency.RUB, "Buyer", BUYER, 555, status,
                               datetime.datetime.now(), "Dota 2, Предметы", None, "")


def make_msg(text, author_id=BUYER, chat_id=555, msg_id=10):
    m = types.Message(msg_id, text, chat_id, "Buyer", BUYER, "Buyer" if author_id else "FunPay", author_id, "",
                      determine_msg_type=False)
    m.type = types.MessageTypes.NON_SYSTEM if author_id else m.get_message_type()
    return m


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.storage = Storage(os.path.join(self.tmp, "storage"))
        cfg = self.storage.config.data
        cfg["funpay"]["golden_key"] = "a" * 32
        cfg["telegram"]["token"] = "123456:" + "A" * 35
        cfg["telegram"]["password_hash"] = hash_password("secret123")
        cfg["telegram"]["admins"] = [42]
        self.storage.config.save()
        self.c = Cardinal(self.storage)
        self.acc = FakeAccount()
        self.c.account = self.acc
        self.c.runner = SimpleNamespace(saved_orders={"ABCD1234": make_order("ABCD1234")})

        from tg_bot.bot import TGBot
        self.tg = TGBot(self.c)
        self.tg.async_send = False  # в тестах проверяем отправку сразу
        self.c.tg = self.tg
        self.tg.bot = MagicMock()
        sent_counter = iter(range(1000, 100000))

        def fake_send(chat_id, text, **kw):
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=next(sent_counter), text=text)
        self.tg.bot.send_message.side_effect = fake_send
        self.tg.bot.reply_to.side_effect = lambda m, text, **kw: fake_send(m.chat.id, text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    def tg_texts(self) -> list[str]:
        texts = [c.args[1] for c in self.tg.bot.send_message.call_args_list]
        texts += [c.args[0] for c in self.tg.bot.edit_message_text.call_args_list]
        texts += [c.args[1] for c in self.tg.bot.reply_to.call_args_list]
        return texts

    def press(self, data: str, user_id: int = 42):
        call = SimpleNamespace(id="1", data=data, from_user=SimpleNamespace(id=user_id),
                               message=SimpleNamespace(chat=SimpleNamespace(id=user_id), message_id=5))
        from tg_bot.base import Ctx
        ctx = Ctx(self.tg.bot, user_id, user_id, 5, call)
        self.tg.dispatch(data, ctx)

    def type_text(self, text: str, user_id: int = 42, reply_to=None, content_type="text"):
        msg = SimpleNamespace(content_type=content_type, text=text, caption=None, chat=SimpleNamespace(id=user_id),
                              from_user=SimpleNamespace(id=user_id, username="admin"), message_id=99,
                              reply_to_message=reply_to)
        self.tg.handle_admin_message(msg)
        return msg

    def new_message_event(self, msg):
        stack = events.MessageEventsStack()
        ev = events.NewMessageEvent("tag", msg, stack)
        stack.add_events([ev])
        return ev


class TestFunPayEvents(BotTestCase):
    def test_greeting_and_notification(self):
        self.c.process_event(self.new_message_event(make_msg("Здравствуйте")))
        self.assertEqual(len(self.acc.sent), 1)
        self.assertIn("Привет, Buyer", self.acc.sent[0][1])
        self.assertTrue(any("Новое сообщение" in t for t in self.tg_texts()))
        # повторное сообщение — без приветствия
        self.c.process_event(self.new_message_event(make_msg("ещё", msg_id=11)))
        self.assertEqual(len(self.acc.sent), 1)

    def test_auto_response_command(self):
        self.storage.set_setting("greetings", False)
        self.c.process_event(self.new_message_event(make_msg("  !ПОМОЩЬ ")))
        self.assertEqual(len(self.acc.sent), 1)
        self.assertIn("Команды", self.acc.sent[0][1])

    def test_blacklist_blocks_auto_response(self):
        self.storage.settings["blacklist"] = ["buyer"]
        self.c.process_event(self.new_message_event(make_msg("!помощь")))
        self.assertEqual(self.acc.sent, [])

    def test_own_message_ignored(self):
        self.c.process_event(self.new_message_event(make_msg("!помощь", author_id=ME)))
        self.assertEqual(self.acc.sent, [])

    def test_auto_delivery_multi(self):
        self.storage.auto_delivery.data["Аркана"] = {"response": "Ваш товар:\n$product", "goods_file": "g.txt",
                                                     "enabled": True, "multi": True}
        self.storage.write_goods("g.txt", ["key1", "key2", "key3"])
        self.c.process_event(events.NewOrderEvent("tag", make_order("ZZZZ0001")))
        self.assertEqual(self.acc.sent[-1], (555, "Ваш товар:\nkey1\nkey2"))
        self.assertEqual(self.storage.read_goods("g.txt"), ["key3"])
        self.assertTrue(any("Новый заказ" in t and "Товар выдан" in t for t in self.tg_texts()))

    def test_auto_delivery_out_of_stock(self):
        self.storage.auto_delivery.data["Аркана"] = {"response": "$product", "goods_file": "g.txt", "enabled": True}
        self.storage.write_goods("g.txt", ["only-one"])
        result = self.c.deliver(make_order("ZZZZ0002"))
        self.assertIn("закончились", result)
        self.assertEqual(self.acc.sent, [])
        self.assertEqual(self.storage.read_goods("g.txt"), ["only-one"])

    def test_auto_delivery_returns_goods_on_failure(self):
        self.storage.auto_delivery.data["Аркана"] = {"response": "$product", "goods_file": "g.txt", "enabled": True}
        self.storage.write_goods("g.txt", ["a", "b", "c"])
        self.c.send_message = lambda *a, **kw: False
        result = self.c.deliver(make_order("ZZZZ0003"))
        self.assertIn("возвращён", result)
        self.assertEqual(self.storage.read_goods("g.txt"), ["a", "b", "c"])

    def test_review_auto_reply(self):
        msg = make_msg("Покупатель Buyer написал отзыв к заказу #ABCD1234.", author_id=0)
        self.assertEqual(msg.type, types.MessageTypes.NEW_FEEDBACK)
        self.c.handle_review(msg)
        self.assertEqual(len(self.acc.reviews), 1)
        self.assertIn("Buyer", self.acc.reviews[0][1])
        self.assertTrue(any("Новый отзыв" in t for t in self.tg_texts()))

    def test_fake_system_message_from_user_ignored(self):
        msg = make_msg("Покупатель Buyer написал отзыв к заказу #ABCD1234.", author_id=BUYER)
        self.storage.set_setting("greetings", False)
        self.c.on_new_message(self.new_message_event(msg))
        self.assertEqual(self.acc.reviews, [])

    def test_order_confirm_reply(self):
        self.storage.set_setting("order_confirm_reply", True)
        msg = make_msg("Покупатель Buyer подтвердил успешное выполнение заказа #ABCD1234 и отправил деньги "
                       "продавцу Seller.", author_id=0)
        self.c.on_new_message(self.new_message_event(msg))
        self.assertEqual(len(self.acc.sent), 1)
        self.assertIn("#ABCD1234", self.acc.sent[0][1])

    def test_raise(self):
        report = self.c.raiser.raise_now()
        self.assertEqual(self.acc.raised, [(1, [10])])
        self.assertIn("поднято", report[0])
        report = self.c.raiser.raise_now()
        self.assertIn("через 2 ч", report[0])

    def test_balance(self):
        self.assertEqual(self.c.get_balance().total_rub, 150.5)


class TestTelegram(BotTestCase):
    def test_auth(self):
        msg = SimpleNamespace(content_type="text", text="wrong", chat=SimpleNamespace(id=7),
                              from_user=SimpleNamespace(id=7, username="x"), message_id=1)
        self.tg.handle_auth(msg)
        self.assertNotIn(7, self.storage.admins)
        msg.text = "secret123"
        self.tg.handle_auth(msg)
        self.assertIn(7, self.storage.admins)

    def test_all_menus_render(self):
        for data in ["menu", "st", "bal", "chats", "chat:555", "orders", "ord:ABCD1234", "lots:0", "lots_r",
                     "lot:777", "lot_f:777", "rs", "rs_now", "ar", "ar_v:0", "rv", "rv_v:5", "gr", "ad", "tp", "nt",
                     "bl", "set", "adm", "tpl:555", "rd", "lot_open", "lot_c:777", "lot_d:777", "ref:ABCD1234", "rst"]:
            self.tg.bot.reset_mock()
            self.press(data)
            errors = [t for t in self.tg_texts() if t.startswith("❌ Ошибка")]
            self.assertFalse(errors, f"{data}: {errors}")

    def test_reply_via_button_and_reply_to(self):
        self.press("rep:555")
        self.type_text("Сейчас сделаю")
        self.assertEqual(self.acc.sent[-1], (555, "Сейчас сделаю"))
        # ответ реплаем на уведомление
        self.c.process_event(self.new_message_event(make_msg("ау")))
        notif_id = max(k[1] for k in self.tg.reply_map)
        self.type_text("Отвечаю реплаем", reply_to=SimpleNamespace(message_id=notif_id))
        self.assertEqual(self.acc.sent[-1], (555, "Отвечаю реплаем"))

    def test_send_photo(self):
        self.press("rep:555")
        self.tg.bot.get_file.return_value = SimpleNamespace(file_path="p.jpg")
        self.tg.bot.download_file.return_value = b"\x89PNG"
        msg = SimpleNamespace(content_type="photo", photo=[SimpleNamespace(file_id="f")], caption="подпись",
                              text=None, chat=SimpleNamespace(id=42), from_user=SimpleNamespace(id=42),
                              message_id=3, reply_to_message=None)
        self.tg.handle_admin_message(msg)
        self.assertEqual(self.acc.images, [(555, b"\x89PNG")])
        self.assertEqual(self.acc.sent[-1], (555, "подпись"))

    def test_send_template(self):
        self.press("tps:555:0")
        self.assertEqual(self.acc.sent[-1][1], self.storage.templates.data[0])

    def test_lot_toggle_and_edit(self):
        self.press("lot_t:777")
        self.assertFalse(self.acc.saved_lots[-1].active)
        self.press("lot_e:777:price")
        self.type_text("149,9")
        self.assertEqual(self.acc.saved_lots[-1].price, 149.9)
        self.press("lot_e:777:raw")
        self.type_text("fields[method]=Подарком")
        self.assertEqual(self.acc.saved_lots[-1].fields["fields[method]"], "Подарком")
        self.assertEqual(self.acc.saved_lots[-1].price, 149.9)

    def test_lot_copy_and_delete(self):
        self.press("lot_cy:777")
        self.assertEqual(self.acc.saved_lots[-1].fields["offer_id"], "0")
        self.press("lot_dy:777")
        self.assertEqual(self.acc.deleted, [777])


    def test_auto_response_crud(self):
        self.press("ar_add")
        self.type_text("!гарантия|гарантия")
        self.type_text("Гарантия 30 дней, $username")
        self.assertIn("!гарантия|гарантия", self.storage.auto_response.data)
        self.assertIsNotNone(self.c.find_command("ГАРАНТИЯ"))

    def test_auto_delivery_setup_from_tg(self):
        self.press("ad_add")
        self.type_text("Аркана")
        self.type_text("Держи: $product")
        idx = list(self.storage.auto_delivery.data).index("Аркана")
        self.press(f"ad_g:{idx}")
        self.type_text("k1\nk2\n\nk3")
        cfg = self.storage.auto_delivery.data["Аркана"]
        self.assertEqual(self.storage.read_goods(cfg["goods_file"]), ["k1", "k2", "k3"])

    def test_refund(self):
        self.press("refy:ABCD1234")
        self.assertEqual(self.acc.refunds, ["ABCD1234"])

    def test_toggles(self):
        before = self.storage.setting("auto_raise")
        self.press("rs_t")
        self.assertNotEqual(before, self.storage.setting("auto_raise"))
        self.press("nt_t:raise")
        self.assertTrue(self.storage.notify_enabled("raise"))
        self.press("set_t:eternal_online")
        self.assertFalse(self.storage.setting("eternal_online"))


class TestUtils(unittest.TestCase):
    def test_format_text(self):
        text = format_text("$username $order_id $order_link", username="Bob", order_id="ABCD1234")
        self.assertEqual(text, "Bob #ABCD1234 https://funpay.com/orders/ABCD1234/")



class TestProxy(BotTestCase):
    def test_parse_formats(self):
        from cardinal.proxy import mask, parse_proxy
        self.assertEqual(parse_proxy("1.2.3.4:1080", "socks5"), "socks5://1.2.3.4:1080")
        self.assertEqual(parse_proxy("1.2.3.4:1080:user:p@ss", "socks5"), "socks5://user:p%40ss@1.2.3.4:1080")
        self.assertEqual(parse_proxy("user:pass@1.2.3.4:8080"), "http://user:pass@1.2.3.4:8080")
        self.assertEqual(parse_proxy("SOCKS5://u:p@host.example:1080/", "http"), "socks5://u:p@host.example:1080")
        self.assertEqual(parse_proxy("socks://1.2.3.4:1"), "socks5://1.2.3.4:1")
        for bad in ("", "1.2.3.4", "ftp://1.2.3.4:21", "1.2.3.4:99999", "user@1.2.3.4:80"):
            with self.assertRaises(ValueError, msg=bad):
                parse_proxy(bad)
        self.assertEqual(mask("socks5://u:secret@1.2.3.4:1080"), "socks5://u:****@1.2.3.4:1080")

    def _ok(self, **kw):
        res = {"ok": True, "ip": "5.6.7.8", "ping": 100, "funpay": True, "telegram": True, "error": None}
        res.update(kw)
        return res

    def test_telegram_proxy(self):
        import telebot
        from tg_bot import panel
        panel.check_proxy = lambda p, **kw: self._ok()
        self.press("px_ty:tg:socks5")
        self.type_text("1.2.3.4:1080:user:pass")
        self.assertEqual(self.storage.get_proxy("telegram"), "socks5://user:pass@1.2.3.4:1080")
        self.assertEqual(self.storage.get_proxy("funpay"), "")
        self.assertEqual(telebot.apihelper.proxy["https"], "socks5://user:pass@1.2.3.4:1080")
        # FunPay при этом без прокси
        self.assertFalse(getattr(self.c.account, "proxy", None))
        self.press("px_offy:tg")
        self.assertEqual(self.storage.get_proxy("telegram"), "")
        self.assertIsNone(telebot.apihelper.proxy)

    def test_bad_telegram_proxy_never_saved(self):
        from tg_bot import panel
        panel.check_proxy = lambda p, **kw: self._ok(telegram=False)
        self.press("px_ty:tg:socks5")
        self.type_text("1.2.3.4:1080")
        self.assertEqual(self.storage.get_proxy("telegram"), "")
        self.press("px_force")  # форс-сохранение для Telegram невозможно
        self.assertEqual(self.storage.get_proxy("telegram"), "")

    def test_telegram_proxy_not_removed_if_blocked(self):
        from tg_bot import panel
        self.c.set_proxy("telegram", "socks5://1.2.3.4:1080")
        panel.check_proxy = lambda p, **kw: self._ok(telegram=False)
        self.press("px_offy:tg")
        self.assertEqual(self.storage.get_proxy("telegram"), "socks5://1.2.3.4:1080")

    def test_funpay_proxy(self):
        from tg_bot import panel
        self.c.account = SimpleNamespace(proxy=None)
        panel.check_proxy = lambda p, **kw: self._ok(funpay=False)
        self.press("px_ty:fp:http")
        self.type_text("1.2.3.4:8080")
        self.assertEqual(self.storage.get_proxy("funpay"), "")
        self.press("px_force")
        self.assertEqual(self.storage.get_proxy("funpay"), "http://1.2.3.4:8080")
        self.assertEqual(self.c.account.proxy["https"], "http://1.2.3.4:8080")
        self.press("px_offy:fp")
        self.assertEqual(self.c.account.proxy, None)

    def test_migration_from_old_config(self):
        import json
        path = os.path.join(self.tmp, "old")
        os.makedirs(path)
        cfg = {"funpay": {"golden_key": "", "proxy": "socks5://9.9.9.9:1"}, "telegram": {"use_proxy": True}}
        with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        st = Storage(path)
        self.assertEqual(st.get_proxy("telegram"), "socks5://9.9.9.9:1")
        self.assertNotIn("use_proxy", st.config.data["telegram"])

    def test_menus(self):
        from tg_bot import panel
        panel.check_proxy = lambda p, **kw: self._ok()
        for data in ("px", "px_set:tg", "px_set:fp", "px_off:tg", "px_chk:tg", "px_chk:fp"):
            self.tg.bot.reset_mock()
            self.press(data)
            self.assertFalse([t for t in self.tg_texts() if t.startswith("❌ Ошибка")], data)

    def tearDown(self):
        import telebot
        from cardinal import proxy
        from tg_bot import panel
        panel.check_proxy = proxy.check_proxy
        telebot.apihelper.proxy = None
        super().tearDown()


class TestRealSocks5(unittest.TestCase):
    """Поднимает локальный SOCKS5-сервер (с логином/паролем) и делает через него HTTP-запрос."""

    def test_request_through_socks5(self):
        import http.server
        import socket
        import socketserver
        import struct
        import threading

        import requests
        from cardinal.proxy import parse_proxy, to_requests

        class Hello(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "15")
                self.end_headers()
                self.wfile.write(b"hello via socks")

            def log_message(self, *a):
                pass

        web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hello)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        used = []

        class Socks5(socketserver.BaseRequestHandler):
            def handle(self):
                s = self.request
                ver, n = s.recv(2)
                methods = s.recv(n)
                assert 2 in methods
                s.sendall(b"\x05\x02")
                s.recv(1)
                user = s.recv(s.recv(1)[0])
                pwd = s.recv(s.recv(1)[0])
                ok = (user, pwd) == (b"user", b"pass")
                s.sendall(b"\x01" + (b"\x00" if ok else b"\x01"))
                if not ok:
                    return
                _, cmd, _, atyp = s.recv(4)
                if atyp == 1:
                    host = socket.inet_ntoa(s.recv(4))
                else:
                    host = s.recv(s.recv(1)[0]).decode()
                port = struct.unpack(">H", s.recv(2))[0]
                used.append((host, port))
                remote = socket.create_connection((host, port))
                s.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", port))

                def pipe(a, b):
                    try:
                        while data := a.recv(4096):
                            b.sendall(data)
                    except OSError:
                        pass
                    finally:
                        try:
                            b.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                threading.Thread(target=pipe, args=(remote, s), daemon=True).start()
                pipe(s, remote)

        socks = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Socks5)
        socks.daemon_threads = True
        threading.Thread(target=socks.serve_forever, daemon=True).start()
        try:
            proxy = parse_proxy(f"127.0.0.1:{socks.server_address[1]}:user:pass", "socks5")
            r = requests.get(f"http://127.0.0.1:{web.server_address[1]}/", proxies=to_requests(proxy), timeout=5)
            self.assertEqual(r.text, "hello via socks")
            self.assertEqual(used, [("127.0.0.1", web.server_address[1])])
        finally:
            socks.shutdown()
            web.shutdown()


class TestUserAgent(BotTestCase):
    def test_set_user_agent(self):
        self.acc.get = MagicMock()
        self.acc.user_agent = None
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
        self.press("ua")
        self.type_text("hello")
        self.assertNotEqual(self.storage.config.data["funpay"]["user_agent"], "hello")
        self.type_text(ua)
        self.assertEqual(self.storage.config.data["funpay"]["user_agent"], ua)
        self.assertEqual(self.acc.user_agent, ua)
        self.acc.get.assert_called_once_with(update_phpsessid=True)


class TestSendNoDuplicates(BotTestCase):
    def test_no_retry_after_unknown_error(self):
        calls = []

        def boom(*a, **kw):
            calls.append(1)
            raise AttributeError("parse failed")
        self.acc.send_message = boom
        self.assertFalse(self.c.send_message(555, "hi"))
        self.assertEqual(len(calls), 1)

    def test_retry_on_connection_error(self):
        import requests
        calls = []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("down")
        self.acc.send_message = flaky
        with unittest.mock.patch("cardinal.core.time.sleep"):
            self.assertTrue(self.c.send_message(555, "hi"))
        self.assertEqual(len(calls), 2)


class TestSpeedSettings(BotTestCase):
    def test_runner_delay_setting(self):
        self.press("rd:3")
        self.assertEqual(self.storage.setting("runner_delay"), 3)

    def test_status_shows_ping(self):
        self.acc.last_runner_ms = 250
        self.press("st")
        self.assertTrue(any("Пинг FunPay: 🟢 250 мс" in t for t in self.tg_texts()))


class TestSpeed(unittest.TestCase):
    def test_keyed_executor_order_and_parallelism(self):
        import threading
        import time as _time
        from cardinal.dispatch import KeyedExecutor
        ex = KeyedExecutor(max_workers=4)
        log = []
        slow_started = threading.Event()

        def slow(tag):
            slow_started.set()
            _time.sleep(0.3)
            log.append(tag)

        def fast(tag):
            log.append(tag)
        ex.submit("chatA", slow, "A1")
        ex.submit("chatA", fast, "A2")  # должен выполниться строго после A1
        slow_started.wait(1)
        ex.submit("chatB", fast, "B1")  # другой чат не ждёт медленный chatA
        _time.sleep(0.1)
        self.assertEqual(log, ["B1"])
        ex.shutdown(wait=True)
        _time.sleep(0.4)
        self.assertEqual(log, ["B1", "A1", "A2"])


class TestNonBlocking(BotTestCase):
    def test_reply_before_notification_and_async_telegram(self):
        import time as _time
        order = []
        self.acc.send_message = lambda chat_id, text, *a, **kw: order.append("funpay")
        self.tg.async_send = True

        def slow_tg(*a, **kw):
            _time.sleep(0.5)
            order.append("telegram")
            return SimpleNamespace(chat=SimpleNamespace(id=42), message_id=1)
        self.tg.bot.send_message.side_effect = slow_tg
        started = _time.time()
        self.c.process_event(self.new_message_event(make_msg("!помощь")))
        self.assertLess(_time.time() - started, 0.3)  # обработка не ждёт Telegram
        self.tg.outbox.join()
        self.assertEqual(order[0], "funpay")  # покупателю ответили раньше, чем ушло уведомление
        self.assertIn("telegram", order)

    def test_fast_mode(self):
        self.storage.set_setting("runner_delay", 6)
        self.assertEqual(self.c.poll_delay(), 6)
        self.c.mark_activity()
        self.assertEqual(self.c.poll_delay(), 2.0)
        self.c.last_activity -= 1000
        self.assertEqual(self.c.poll_delay(), 6)

    def test_old_default_delay_migrated(self):
        import json
        path = os.path.join(self.tmp, "old_delay")
        os.makedirs(path)
        with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"settings": {"runner_delay": 6}}, f)
        self.assertEqual(Storage(path).setting("runner_delay"), 4)


class TestFunPayAdapter(unittest.TestCase):
    def test_dynamic_delay_shim(self):
        import FunPayAPI.updater.runner as runner_module
        from cardinal import fp
        original = runner_module.time
        slept = []
        try:
            with unittest.mock.patch("cardinal.fp.time.sleep", side_effect=slept.append):
                sentinel = fp.install_dynamic_delay(lambda: 3.0)
                clock = runner_module.time
                clock.sleep(sentinel - 0.5)  # как в Runner.listen: delay - время итерации (0.5 c)
                clock.sleep(sentinel)        # ветка после 429
                clock.sleep(0.1)             # обычные паузы библиотеки не трогаем
            self.assertEqual(slept, [2.5, 3.0, 0.1])
            self.assertIs(clock.time, __import__("time").time)
        finally:
            runner_module.time = original

    def test_configure_account(self):
        from FunPayAPI import Account
        from cardinal.fp import configure_account
        acc = configure_account(Account("g" * 32))
        self.assertFalse(acc.session.trust_env)  # системный прокси / VPN не используется для FunPay
        self.assertEqual(acc.requests_timeout, (5, 20))
        retry = acc.session.get_adapter("https://funpay.com").max_retries
        self.assertEqual((retry.connect, retry.read), (3, 0))  # POST не повторяется после таймаута чтения
        self.assertTrue(configure_account(Account("g" * 32), use_system_proxy=True).session.trust_env)
        with unittest.mock.patch.object(Account, "method", return_value=SimpleNamespace(status_code=200)):
            acc.csrf_token = "t"
            acc.runner_request({"objects": [], "request": None})
        self.assertIsInstance(acc.last_runner_ms, int)

    def test_system_proxy_ignored_on_real_request(self):
        import http.server
        import threading
        from FunPayAPI import Account
        from cardinal.fp import configure_account

        class Ok(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass
        web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ok)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        bad = "socks5://127.0.0.1:9"
        env = {"HTTP_PROXY": bad, "HTTPS_PROXY": bad, "ALL_PROXY": bad, "http_proxy": bad, "all_proxy": bad,
               "NO_PROXY": "", "no_proxy": ""}
        try:
            with unittest.mock.patch.dict(os.environ, env):
                acc = configure_account(Account("g" * 32))
                r = acc.session.get(f"http://127.0.0.1:{web.server_address[1]}/", timeout=5)
                self.assertEqual(r.text, "ok")
        finally:
            web.shutdown()


class TestLotsWithCardinalApi(BotTestCase):
    def test_deep_refresh_finds_disabled_lots(self):
        lots = {lot["id"]: lot for lot in self.c.lots.all_lots(deep=True)}
        self.assertTrue(lots[777]["active"])
        self.assertFalse(lots[888]["active"])
        self.assertEqual(lots[888]["title"], "Выключенный лот")
        self.assertEqual(lots[888]["price"], 50.0)


class TestFunPaySystemProxyToggle(BotTestCase):
    def test_toggle(self):
        self.c.account = SimpleNamespace(session=SimpleNamespace(trust_env=False))
        self.press("px_sys")
        self.assertTrue(self.storage.config.data["funpay"]["use_system_proxy"])
        self.assertTrue(self.c.account.session.trust_env)
        self.press("px_sys")
        self.assertFalse(self.c.account.session.trust_env)
