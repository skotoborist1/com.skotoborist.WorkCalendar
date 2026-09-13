#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, GObject, Gdk, Pango
import json, os, datetime, threading
import urllib.request, urllib.error, base64

APP_ID = "com.skotoborist.WorkCalendar"
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "work-calendar")
DATA_FILE = os.path.join(DATA_DIR, "work-calendar-export.json")
WEBDAV_CONFIG_FILE = os.path.join(DATA_DIR, "webdav.conf")
os.makedirs(DATA_DIR, exist_ok=True)

# Обновленный блок стилей для комфортного чтения заметок
CSS = """
.day-cell {
    background: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 12px;
    padding: 8px;
    min-height: 90px;
}
.day-cell:hover { border-color: @accent_color; }
.other-month { opacity: 0.4; }
.today-cell { border: 2px solid @accent_color; }

.today-number {
    background: @accent_bg_color;
    color: white;
    font-weight: bold;
    border-radius: 999px;
    min-width: 28px;
    min-height: 28px;
    padding: 2px;
}

.event-pill {
    /* Оставляем ваш стандартный полупрозрачный фон */
    background: alpha(@accent_color, 0.15);
    border-left: 3px solid @accent_color;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 14px;
    margin-bottom: 2px;
    
    /* РЕШЕНИЕ ПРОБЛЕМЫ: */
    /* Используем системную переменную цвета текста общего интерфейса. */
    /* В светлой теме @window_fg_color превращается в черный, а в темной — в белый! */
    color: @window_fg_color;
}

.event-more { font-size: 11px; color: @dim_label_color; padding: 0 6px; }
.weekday-label { font-weight: bold; color: @dim_label_color; font-size: 12px; }
.month-label { font-size: 16px; font-weight: bold; }
.dim-label { color: @dim_label_color; }
.popover-entry { margin-bottom: 8px; }
.popover-add-btn { margin-bottom: 8px; }
.popover-event-row { padding: 4px 0; }
.popover-separator { margin: 8px 0; }
.sync-label { font-size: 12px; color: @dim_label_color; padding: 0 8px; }
""".encode('utf-8')



MONTH_NAMES = [
    'Январь','Февраль','Март','Апрель','Май','Июнь',
    'Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'
]
WEEKDAY_NAMES = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']


class DayCell(Gtk.Box):
    __gsignals__ = {
        'day-clicked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, year, month, day, is_other, is_today):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.date_key = f"{year}-{month+1:02d}-{day:02d}"
        self.add_css_class("day-cell")
        if is_other: self.add_css_class("other-month")
        if is_today: self.add_css_class("today-cell")

        num_box = Gtk.Box()
        num_box.set_halign(Gtk.Align.CENTER)
        num_label = Gtk.Label(label=str(day))
        if is_today:
            num_label.add_css_class("today-number")
        num_box.append(num_label)
        self.append(num_box)

        self.events_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.events_box.set_hexpand(True)
        self.events_box.set_vexpand(True)
        self.append(self.events_box)

        gesture = Gtk.GestureClick()
        gesture.connect("released", self._on_click)
        self.add_controller(gesture)

        self._popover = None

    def _on_click(self, gesture, n_press, x, y):
        self.emit("day-clicked", self.date_key)

    def update_events(self, events):
        while child := self.events_box.get_first_child():
            self.events_box.remove(child)
        day_events = events.get(self.date_key, [])
        for ev in day_events[:3]:
            pill = Gtk.Label(label=ev["text"])
            pill.add_css_class("event-pill")
            pill.set_xalign(0.0)
            pill.set_hexpand(True)
            pill.set_ellipsize(Pango.EllipsizeMode.END)
            self.events_box.append(pill)
        if len(day_events) > 3:
            more = Gtk.Label(label=f"+{len(day_events)-3} ещё")
            more.add_css_class("event-more")
            more.set_xalign(0.0)
            self.events_box.append(more)

    def show_popover(self, date_label, events, app):
        if self._popover is not None:
            self._popover.unparent()
            self._popover = None

        self._popover = EventPopover(self, self.date_key, date_label, events, app)
        self._popover.present()
class EventPopover(Gtk.Popover):
    def __init__(self, parent_cell, date_key, date_label, events, app):
        super().__init__()
        self.set_parent(parent_cell)
        self.set_position(Gtk.PositionType.BOTTOM)
        self.set_autohide(True)
        self.date_key = date_key
        self.events = events
        self.app = app

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_size_request(300, -1)

        title = Gtk.Label(label=f"<b>{date_label}</b>")
        title.set_use_markup(True)
        title.set_halign(Gtk.Align.START)
        title.set_margin_bottom(8)
        main_box.append(title)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry_row.add_css_class("popover-entry")

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Новое событие...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda w: self._add_event())
        entry_row.append(self.entry)

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda w: self._add_event())
        entry_row.append(add_btn)

        main_box.append(entry_row)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("popover-separator")
        main_box.append(sep)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_max_content_height(250)
        scrolled.set_propagate_natural_height(True)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.set_child(self.list_box)
        main_box.append(scrolled)

        self.set_child(main_box)
        self._refresh_list()

        self.connect("closed", self._on_closed)

        self.popup()
        self.entry.grab_focus()

    def _add_event(self):
        text = self.entry.get_text().strip()
        if not text:
            return
        if self.date_key not in self.events:
            self.events[self.date_key] = []
        self.events[self.date_key].append(
            {"id": int(datetime.datetime.now().timestamp() * 1000), "text": text}
        )
        self.entry.set_text("")
        self.app.save_events()
        self._refresh_list()
        self.app.render_calendar()
        self.entry.grab_focus()

    def _refresh_list(self):
        while child := self.list_box.get_first_child():
            self.list_box.remove(child)
        day_events = self.events.get(self.date_key, [])
        if not day_events:
            empty = Gtk.Label(label="Нет событий")
            empty.add_css_class("dim-label")
            empty.set_margin_top(8)
            self.list_box.append(empty)
            return
        for i, ev in enumerate(day_events):
            row = Gtk.Box(spacing=8)
            row.add_css_class("popover-event-row")

            label = Gtk.Label(label=ev["text"])
            label.set_hexpand(True)
            label.set_xalign(0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(label)

            del_btn = Gtk.Button()
            del_btn.set_icon_name("window-close-symbolic")
            del_btn.add_css_class("destructive-action")
            del_btn.add_css_class("circular")
            del_btn.connect("clicked", self._make_delete(i))
            row.append(del_btn)

            self.list_box.append(row)

    def _make_delete(self, idx):
        def _delete(widget):
            day_events = self.events.get(self.date_key, [])
            if idx < len(day_events):
                day_events.pop(idx)
                if not day_events:
                    del self.events[self.date_key]
                self.app.save_events()
                self._refresh_list()
                self.app.render_calendar()
        return _delete

    def _on_closed(self, popover):
        self.unparent()


class WebDAVSettingsDialog(Adw.Window):
    def __init__(self, parent, app):
        super().__init__(transient_for=parent)
        self.set_title("Настройки WebDAV")
        self.set_default_size(420, 400)
        self.set_modal(True)
        self.app = app

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        main_box.append(header)

        save_btn = Gtk.Button(label="Сохранить")
        save_btn.add_css_class("suggested-action")
        header.pack_end(save_btn)
        save_btn.connect("clicked", lambda w: self._save())

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)

        url_label = Gtk.Label(label="Адрес WebDAV")
        url_label.set_halign(Gtk.Align.START)
        content.append(url_label)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("https://example.com")
        self.url_entry.set_text(app.webdav_config.get("url", ""))
        content.append(self.url_entry)

        user_label = Gtk.Label(label="Логин")
        user_label.set_halign(Gtk.Align.START)
        content.append(user_label)
        self.user_entry = Gtk.Entry()
        self.user_entry.set_text(app.webdav_config.get("username", ""))
        content.append(self.user_entry)

        pass_label = Gtk.Label(label="Пароль")
        pass_label.set_halign(Gtk.Align.START)
        content.append(pass_label)
        self.pass_entry = Gtk.Entry()
        self.pass_entry.set_visibility(False)
        self.pass_entry.set_text(app.webdav_config.get("password", ""))
        content.append(self.pass_entry)

        show_pass = Gtk.CheckButton(label="Показать пароль")
        show_pass.connect("toggled", lambda w: self.pass_entry.set_visibility(w.get_active()))
        content.append(show_pass)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        sep.set_margin_bottom(8)
        content.append(sep)

        info = Gtk.Label(
            label="Синхронизация выполняется автоматически:\n"
                 "• при запуске — загрузка с сервера\n"
                 "• при выходе — выгрузка на server"
        )
        info.set_halign(Gtk.Align.START)
        info.add_css_class("dim-label")
        info.set_wrap(True)
        content.append(info)

        main_box.append(content)
        self.set_content(main_box)

    def _save(self):
        self.app.webdav_config["url"] = self.url_entry.get_text().strip().rstrip("/")
        self.app.webdav_config["username"] = self.user_entry.get_text().strip()
        self.app.webdav_config["password"] = self.pass_entry.get_text()
        self.app._save_webdav_config()
        self.close()
class WorkCalendarApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.events = {}
        self.current_date = datetime.date.today()
        self.is_dark = False
        self.webdav_config = {"url": "", "username": "", "password": ""}
        self._syncing = False

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._load_webdav_config()
        self.load_events()
        self._load_theme_pref()
        self._apply_theme()

        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("Work Calendar")
        self.win.set_default_size(1100, 720)

        self.toast_overlay = Adw.ToastOverlay()
        self.win.set_content(self.toast_overlay)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        header = Adw.HeaderBar()
        main_box.append(header)

        prev_btn = Gtk.Button()
        prev_btn.set_icon_name("go-previous-symbolic")
        prev_btn.connect("clicked", lambda w: self._change_month(-1))
        header.pack_start(prev_btn)

        self.month_label = Gtk.Label()
        self.month_label.add_css_class("month-label")
        self.month_label.set_margin_start(12)
        self.month_label.set_margin_end(12)
        header.pack_start(self.month_label)

        next_btn = Gtk.Button()
        next_btn.set_icon_name("go-next-symbolic")
        next_btn.connect("clicked", lambda w: self._change_month(1))
        header.pack_start(next_btn)

        today_btn = Gtk.Button(label="Сегодня")
        today_btn.set_margin_start(6)
        today_btn.connect("clicked", lambda w: self._go_today())
        header.pack_start(today_btn)

        self.sync_label = Gtk.Label(label="")
        self.sync_label.add_css_class("sync-label")
        header.pack_end(self.sync_label)

        self.theme_btn = Gtk.Button()
        self._update_theme_icon()
        self.theme_btn.connect("clicked", lambda w: self._toggle_theme())
        header.pack_end(self.theme_btn)

        menu = Gio.Menu()
        menu.append("Экспортировать в файл", "app.export")
        menu.append("Импортировать из файла", "app.import")
        section1 = Gio.Menu()
        menu.append_section(None, section1)
        section1.append("Синхронизировать сейчас", "app.sync-now")
        section1.append("Настройки WebDAV", "app.webdav-settings")
        section2 = Gio.Menu()
        menu.append_section(None, section2)
        section2.append("Удалить все события", "app.delete-all")
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        self._setup_actions()

        cal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cal_box.set_margin_start(16)
        cal_box.set_margin_end(16)
        cal_box.set_margin_top(12)
        cal_box.set_margin_bottom(16)
        main_box.append(cal_box)

        wd_box = Gtk.Box(spacing=6)
        for name in WEEKDAY_NAMES:
            lbl = Gtk.Label(label=name)
            lbl.add_css_class("weekday-label")
            lbl.set_hexpand(True)
            wd_box.append(lbl)
        cal_box.append(wd_box)

        self.grid = Gtk.Grid()
        self.grid.set_row_spacing(6)
        self.grid.set_column_spacing(6)
        self.grid.set_column_homogeneous(True)
        cal_box.append(self.grid)

        self.render_calendar()
        self.win.present()

        # Автосинхронизация при запуске
        self._sync_from_webdav_async()

    def do_shutdown(self):
        # Автосинхронизация при выходе
        if self._webdav_configured() and not self._syncing:
            self.save_events()
            self._webdav_upload(timeout=5)
        Adw.Application.do_shutdown(self)

    def _setup_actions(self):
        for name, handler in [("export", self._on_export),
                              ("import", self._on_import),
                              ("delete-all", self._on_delete_all),
                              ("sync-now", self._on_sync_now),
                              ("webdav-settings", self._on_webdav_settings)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    # ── WebDAV ──

    def _load_webdav_config(self):
        if os.path.exists(WEBDAV_CONFIG_FILE):
            try:
                with open(WEBDAV_CONFIG_FILE, "r") as f:
                    self.webdav_config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    def _save_webdav_config(self):
        with open(WEBDAV_CONFIG_FILE, "w") as f:
            json.dump(self.webdav_config, f, ensure_ascii=False)

    def _webdav_configured(self):
        return bool(
            self.webdav_config.get("url")
            and self.webdav_config.get("username")
        )

    def _webdav_file_url(self):
        url = self.webdav_config.get("url", "").rstrip("/")
        return f"{url}/work-calendar-export.json"

    def _webdav_auth_header(self):
        creds = f"{self.webdav_config['username']}:{self.webdav_config.get('password', '')}"
        encoded = base64.b64encode(creds.encode()).decode()
        return f"Basic {encoded}"

    def _webdav_download(self, timeout=10):
        url = self._webdav_file_url()
        if not url:
            return None
        req = urllib.request.Request(url)
        req.add_header("Authorization", self._webdav_auth_header())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # Файл ещё не загружен на сервер
            return None
        except Exception:
            return None

    def _webdav_upload(self, timeout=10):
        url = self._webdav_file_url()
        if not url:
            return False
        data = json.dumps(self.events, ensure_ascii=False, indent=2)
        req = urllib.request.Request(url, data=data.encode("utf-8"), method="PUT")
        req.add_header("Authorization", self._webdav_auth_header())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status in (200, 201, 204)
        except Exception:
            return False

    def _sync_from_webdav_async(self):
        if not self._webdav_configured() or self._syncing:
            return
        self._syncing = True
        self.sync_label.set_label("⟳ Синхронизация...")

        def _sync():
            remote_data = self._webdav_download()
            if remote_data is not None:
                try:
                    remote_events = json.loads(remote_data)
                    if isinstance(remote_events, dict):
                        self.events = remote_events
                        self.save_events()
                        GLib.idle_add(self.render_calendar)
                        GLib.idle_add(lambda: self.sync_label.set_label("✓ Облако"))
                        GLib.idle_add(lambda: self._show_toast("Данные загружены с WebDAV"))
                    else:
                        GLib.idle_add(lambda: self.sync_label.set_label(""))
                except json.JSONDecodeError:
                    GLib.idle_add(lambda: self.sync_label.set_label(""))
            else:
                GLib.idle_add(lambda: self.sync_label.set_label(""))
            self._syncing = False

        threading.Thread(target=_sync, daemon=True).start()

    def _on_sync_now(self, action, param):
        if not self._webdav_configured():
            self._show_toast("Сначала настройте WebDAV в меню")
            return
        if self._syncing:
            return
        self._syncing = True
        self.sync_label.set_label("⟳ Синхронизация...")

        def _sync():
            # Сначала скачиваем
            remote_data = self._webdav_download()
            if remote_data is not None:
                try:
                    remote_events = json.loads(remote_data)
                    if isinstance(remote_events, dict):
                        self.events = remote_events
                        self.save_events()
                        GLib.idle_add(self.render_calendar)
                except json.JSONDecodeError:
                    pass
            # Потом выгружаем
            ok = self._webdav_upload()
            GLib.idle_add(lambda: self.sync_label.set_label("✓ Облако" if ok else ""))
            GLib.idle_add(lambda: self._show_toast(
                "Синхронизация выполнена" if ok else "Ошибка синхронизации"))
            self._syncing = False

        threading.Thread(target=_sync, daemon=True).start()

    def _on_webdav_settings(self, action, param):
        WebDAVSettingsDialog(self.win, self).present()
    # ── Тема ──

    def _load_theme_pref(self):
        f = os.path.join(DATA_DIR, "theme.pref")
        if os.path.exists(f):
            with open(f, "r") as fh:
                self.is_dark = fh.read().strip() == "dark"

    def _apply_theme(self):
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if self.is_dark
            else Adw.ColorScheme.FORCE_LIGHT
        )

    def _update_theme_icon(self):
        self.theme_btn.set_label("☀️" if self.is_dark else "🌙")

    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        self._apply_theme()
        self._update_theme_icon()
        with open(os.path.join(DATA_DIR, "theme.pref"), "w") as f:
            f.write("dark" if self.is_dark else "light")

    # ── Навигация ──

    def _change_month(self, delta):
        y = self.current_date.year
        m = self.current_date.month + delta
        while m < 1: m += 12; y -= 1
        while m > 12: m -= 12; y += 1
        self.current_date = datetime.date(y, m, 1)
        self.render_calendar()

    def _go_today(self):
        self.current_date = datetime.date.today()
        self.render_calendar()

    # ── Календарь ──

    def render_calendar(self):
        while child := self.grid.get_first_child():
            self.grid.remove(child)
        y = self.current_date.year
        m = self.current_date.month
        self.month_label.set_label(f"{MONTH_NAMES[m-1]} {y}")
        today = datetime.date.today()

        first = datetime.date(y, m, 1)
        start_wd = first.weekday()
        if m == 12:
            dim = (datetime.date(y+1, 1, 1) - first).days
        else:
            dim = (datetime.date(y, m+1, 1) - first).days
        prev_dim = (first - datetime.timedelta(days=1)).day

        row = 0; col = 0
        for i in range(start_wd):
            d = prev_dim - start_wd + i + 1
            pm = 12 if m == 1 else m - 1
            py = y - 1 if m == 1 else y
            cell = DayCell(py, pm-1, d, True, False)
            cell.update_events(self.events)
            cell.connect("day-clicked", self._on_day_clicked)
            self.grid.attach(cell, col, row, 1, 1); col += 1

        for d in range(1, dim + 1):
            is_today = (y == today.year and m == today.month and d == today.day)
            cell = DayCell(y, m-1, d, False, is_today)
            cell.update_events(self.events)
            cell.connect("day-clicked", self._on_day_clicked)
            self.grid.attach(cell, col, row, 1, 1)
            col += 1
            if col == 7: col = 0; row += 1

        nd = 1
        while col > 0 and col < 7:
            nm = 1 if m == 12 else m + 1
            ny = y + 1 if m == 12 else y
            cell = DayCell(ny, nm-1, nd, True, False)
            cell.update_events(self.events)
            cell.connect("day-clicked", self._on_day_clicked)
            self.grid.attach(cell, col, row, 1, 1); col += 1; nd += 1

    def _on_day_clicked(self, cell, date_key):
        parts = date_key.split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        label = f"{d} {MONTH_NAMES[m-1].lower()} {y}"
        cell.show_popover(label, self.events, self)

    # ── Данные ──

    def load_events(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.events = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.events = {}
        else:
            self.events = {}

    def save_events(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    def _show_toast(self, msg):
        self.toast_overlay.add_toast(Adw.Toast.new(msg))

    # ── Экспорт / Импорт / Удаление ──

    def _on_export(self, action, param):
        dialog = Gtk.FileDialog()
        dialog.set_title("Экспорт календаря")
        dialog.set_initial_name("work-calendar-export.json")
        ff = Gtk.FileFilter()
        ff.set_name("JSON"); ff.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dialog.set_filters(filters)

        def _on_save(d, result):
            try:
                f = d.save_finish(result)
                if f:
                    with open(f.get_path(), "w", encoding="utf-8") as fh:
                        json.dump(self.events, fh, ensure_ascii=False, indent=2)
                    self._show_toast("Файл экспортирован")
            except GLib.Error: pass
        dialog.save(self.win, None, _on_save)

    def _on_import(self, action, param):
        dialog = Gtk.FileDialog()
        dialog.set_title("Импорт календаря")
        ff = Gtk.FileFilter()
        ff.set_name("JSON"); ff.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dialog.set_filters(filters)

        def _on_open(d, result):
            try:
                f = d.open_finish(result)
                if f:
                    with open(f.get_path(), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if isinstance(data, dict):
                        self.events = data
                        self.save_events()
                        self.render_calendar()
                        self._show_toast("Файл импортирован")
                    else:
                        self._show_toast("Ошибка: неверный формат")
            except (GLib.Error, json.JSONDecodeError):
                self._show_toast("Ошибка при чтении файла")
        dialog.open(self.win, None, _on_open)

    def _on_delete_all(self, action, param):
        dlg = Adw.MessageDialog(
            transient_for=self.win,
            heading="Удалить все события?",
            body="Это действие нельзя отменить."
        )
        dlg.add_response("cancel", "Отмена")
        dlg.add_response("delete", "Удалить")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def _on_resp(d, r):
            if r == "delete":
                self.events = {}
                self.save_events()
                self.render_calendar()
                self._show_toast("Все события удалены")
        dlg.connect("response", _on_resp)
        dlg.present()


if __name__ == "__main__":
    app = WorkCalendarApp()
    app.run()

