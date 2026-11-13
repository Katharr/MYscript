"""Isolated native Qt visual check; never operates game windows or user config."""
import sys, copy, tempfile, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import QLabel
from mhxy.core import config as C
from mhxy.qtui.main import get_application
from mhxy.qtui.window import MainWindow
from mhxy.qtui.role_strip import WindowState
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
tmp = tempfile.TemporaryDirectory()
C.CONFIG_PATH = Path(tmp.name)/'config.json'
C.CONFIG_BACKUP_DIR = Path(tmp.name)/'backups'
patch('mhxy.core.config.load_config', side_effect=lambda: copy.deepcopy(C.DEFAULT_CONFIG)).start()
patch('mhxy.core.config.save_config', side_effect=RuntimeError('visual check forbids saving')).start()
patch.object(MainWindow, 'refresh_windows').start()
app = get_application(['visual-check'])
w = MainWindow(QSettings(str(Path(tmp.name)/'ui.ini'), QSettings.Format.IniFormat))
w.scan_timer.stop(); w.runtime_timer.stop()
w.resize(1120,720); w.show(); w.show_page('daily')
w._apply_windows([WindowState(101,'测试角色甲（长名称）','ready'), WindowState(102,'测试角色乙','ready')], '')
for i in range(30): w.log_panel.append(f'检查消息 {i}：中文笔画、参数与日志阅读。', 'error' if i==29 else 'info', '测试')
meta = dict(platform=app.platformName(), dpi=w.screen().logicalDotsPerInch(), dpr=w.devicePixelRatioF(), font=app.font().toString(), resolved=QFontInfo(app.font()).family(), widgets=[(l.text()[:12],l.font().toString(),QFontInfo(l.font()).family()) for l in w.findChildren(QLabel)][:20])
(out/'environment.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
def captures():
    checks = []
    for width,height in [(1120,720),(800,640),(1440,900)]:
        w.resize(width,height)
        app.processEvents()
        for key in ['daily','escort','general','role_window','settings','calibration']:
            w.show_page(key); app.processEvents()
            work = w.log_workspace
            work.open_log(); app.processEvents()
            for button in (w.window_tools.wake_button,w.window_tools.close_button,w.log_button,w.log_stop_button):
                hit = w.childAt(button.mapTo(w,button.rect().center()))
                assert hit is button, (key,width,button.text(),hit)
            top = w.log_panel.mapTo(w,w.log_panel.rect().topLeft()).y()
            bottom = w.log_panel.mapTo(w,w.log_panel.rect().bottomLeft()).y()
            assert top == 0 and bottom == w.height()-1, (top,bottom,w.height())
            w.grab().save(str(out/f'{width}-{key}.png'))
            checks.append(dict(size=[w.width(),w.height()],page=key,overlay=work.overlay_mode,full_height=True,hit_test=True))
            if width == 800:
                work.close_log(); app.processEvents()
                w.grab().save(str(out/f'{width}-{key}-closed.png'))
    w.resize(800,640)
    w.show_page('escort'); app.processEvents(); w.log_workspace.open_log()
    page = w.pages['escort']
    page.runner = page._make_runner()
    page._start_pending = True
    for phase in ('running','stopping'):
        if phase == 'stopping': page.runner.stop_event.set()
        page._set_status(phase); w._update_strip(); app.processEvents()
        assert not w.task_tabs.isEnabled()
        assert w.log_stop_button.isEnabled()
        assert w.childAt(w.log_stop_button.mapTo(w,w.log_stop_button.rect().center())) is w.log_stop_button
        w.grab().save(str(out/f'800-{phase}.png'))
    page._start_pending = False
    page.runner = None
    page._set_status('idle'); w._update_strip()
    for count in (0,5):
        w._apply_windows([WindowState(201+i,f'测试角色长名称{i}','ready') for i in range(count)],'')
        app.processEvents()
        w.grab().save(str(out/f'800-roles-{count}.png'))
    (out/'checks.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    w.resize(800,640); w.show_page('escort'); app.processEvents(); w.log_workspace.open_log()
    if '--auto-exit' in sys.argv: QTimer.singleShot(100,app.quit)
QTimer.singleShot(1500,captures)
QTimer.singleShot(90000,app.quit)
app.exec()
w.close()
