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
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from FunPayAPI import types  # noqa: E402
from FunPayAPI.common.enums import OrderStatuses, SubCategoryTypes  # noqa: E402
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
        self.sent: list[tuple] = []
        self.images: list[tuple] = []
        self.reviews: list[tuple] = []
        self.saved_lots: list[types.LotFields] = []
        self.deleted: list[int] = []
        self.refunds: list[str] = []
        self.raised: list[tuple] = []
        self.chats = {555: types.ChatShortcut(555, "Buyer", "привет", True, "")}
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
        return types.Message(1, text, chat_id, chat_name, self.username, ME, "")

    def send_image(self, chat_id, image, chat_name=None, *a, **kw):
        self.images.append((chat_id, image))

    def get_chat_by_name(self, name, make_request=False):
        return next((c for c in self.chats.values() if c.name == name), None)

    def get_chat_by_id(self, chat_id, make_request=False):
        return self.chats.get(chat_id)

    def get_chats(self, update=False):
        return self.chats

    def get_chat_history(self, chat_id, interlocutor_username=None, **kw):
        return [types.Message(1, "привет", chat_id, "Buyer", "Buyer", BUYER, "")]

    # --- заказы / отзывы
    def get_order(self, order_id):
        review = types.Review(5, "Всё супер", None, False, "", order_id)
        return types.Order(order_id, OrderStatuses.PAID, self.subcat, "Аркана на Pudge", "full", 100.0,
                           BUYER, "Buyer", ME, self.username, "", review)

    def get_sells(self, **kw):
        return None, [make_order("ABCD1234")]

    def send_review(self, order_id, text, rating=5):
        self.reviews.append((order_id, text))

    def refund(self, order_id):
        self.refunds.append(order_id)

    # --- лоты
    def get_user(self, user_id):
        profile = types.UserProfile(user_id, self.username, "", True, False, "")
        lot = types.LotShortcut(777, None, "Аркана на Pudge", 100.0, self.subcat, "")
        profile.add_lot(lot)
        return profile

    def get_lot_fields(self, lot_id):
        return types.LotFields(lot_id, dict(self.lot_fields[lot_id]))

    def save_lot(self, fields):
        fields.renew_fields()
        self.saved_lots.append(fields)
        if fields.lot_id:
            self.lot_fields[fields.lot_id] = dict(fields.fields)

    def delete_lot(self, lot_id):
        self.deleted.append(lot_id)

    def get_my_subcategory_lot_ids(self, subcategory_id):
        return [777, 888]

    def get_new_lot_form(self, node_id):
        fields = types.LotFields(0, {"offer_id": "0", "node_id": str(node_id), "fields[type]": "a"})
        selects = {"fields[type]": {"label": "Тип", "options": [("a", "Аккаунт"), ("b", "Буст")]}}
        return fields, selects

    def get_balance(self, lot_id=0):
        return types.Balance(150.5, 100.0, 1.0, 1.0, 0.0, 0.0)

    def raise_lots_raw(self, game_id, node_ids):
        self.raised.append((game_id, node_ids))
        if len(self.raised) > 1:
            from FunPayAPI.common.exceptions import RaiseError
            response = SimpleNamespace(status_code=200, request=SimpleNamespace(url="", headers={}, body=""))
            raise RaiseError(response, types.Category(game_id, ""), "Подождите 2 часа.", 7200)
        return True

    def get_raise_info(self, subcategory_id):
        return 1

    @property
    def subcategories(self):
        return [self.subcat]


def make_order(order_id, description="Аркана на Pudge, 2 шт.", status=OrderStatuses.PAID):
    return types.OrderShortcut(order_id, description, 200.0, "Buyer", BUYER, status, datetime.datetime.now(),
                               "Dota 2, Предметы", "")


def make_msg(text, author_id=BUYER, chat_id=555, msg_id=10):
    m = types.Message(msg_id, text, chat_id, "Buyer", "Buyer" if author_id else "FunPay", author_id, "",
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
                     "bl", "set", "adm", "tpl:555", "lot_c:777", "lot_d:777", "ref:ABCD1234", "rst"]:
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

    def test_new_lot_wizard(self):
        self.press("lot_new")
        self.type_text("https://funpay.com/lots/1142/")
        self.type_text("Буст рейтинга")
        self.type_text("Подробно")
        self.type_text("500")
        self.type_text("-")
        self.press("nls:1")
        self.press("nlc")
        created = self.acc.saved_lots[-1]
        self.assertEqual(created.fields["node_id"], "1142")
        self.assertEqual(created.fields["fields[type]"], "b")
        self.assertEqual(created.title_ru, "Буст рейтинга")
        self.assertEqual(created.price, 500.0)
        self.assertTrue(created.active)

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

    def test_lot_form_parsing(self):
        from FunPayAPI.account import Account
        html = """<form>
            <input type="hidden" name="csrf_token" value="tok">
            <input type="hidden" name="offer_id" value="5">
            <input name="price" value="10">
            <input type="checkbox" name="active" checked>
            <input type="checkbox" name="auto_delivery">
            <input type="submit">
            <textarea name="fields[desc][ru]">desc</textarea>
            <div class="form-group"><label>Сервер</label>
              <select name="server"><option value="">-</option><option value="1">EU</option></select></div>
            <select name="fields[x]"><option value="a">A</option><option value="b" selected>B</option></select>
        </form>"""
        fields = Account._parse_lot_form(html)
        self.assertEqual(fields["price"], "10")
        self.assertEqual(fields["active"], "on")
        self.assertNotIn("auto_delivery", fields)
        self.assertEqual(fields["server"], "")
        self.assertEqual(fields["fields[x]"], "b")
        self.assertEqual(fields["fields[desc][ru]"], "desc")
        selects = Account._parse_lot_form_selects(html)
        self.assertEqual(selects["server"]["label"], "Сервер")
        self.assertEqual(selects["server"]["options"], [("1", "EU")])


if __name__ == "__main__":
    unittest.main()
