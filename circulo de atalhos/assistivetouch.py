import sys
import time
import json
import requests
import threading
from io import BytesIO
from PIL import Image, ImageFilter
from firebase_config import FIREBASE_CONFIG, SETORES
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QTextEdit, QMessageBox, QScrollArea,
                              QListWidget, QListWidgetItem, QSpinBox, QComboBox, QCheckBox,
                              QTabWidget, QFrame)
from PyQt6.QtCore import (Qt, QPoint, QTimer, QObject, pyqtSignal, QRect,
                           QPropertyAnimation, QEasingCurve, QSize, pyqtProperty,
                           QByteArray, QBuffer, QIODevice, QSequentialAnimationGroup)
from PyQt6.QtGui import (QCursor, QPainter, QColor, QPen, QRadialGradient,
                          QFont, QPixmap, QIcon, QPainterPath)
from PyQt6.QtSvg import QSvgRenderer
from pynput import keyboard, mouse
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController


# ---------------------------------------------------------------------------
# NotificationWidget
# ---------------------------------------------------------------------------
class NotificationWidget(QWidget):
    def __init__(self, message):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background-color: #88c22b;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(15, 10, 15, 10)
        label = QLabel(message)
        label.setStyleSheet("color: white; font-size: 12px; font-weight: bold;")
        layout.addWidget(label)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        self.adjustSize()

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 50)

        QTimer.singleShot(2000, self.close)
        self.setWindowOpacity(1.0)
        self.opacity = 1.0
        self.fade_timer = QTimer()
        self.fade_timer.timeout.connect(self.fade_out)
        QTimer.singleShot(1500, self.fade_timer.start)

    def fade_out(self):
        self.opacity -= 0.1
        if self.opacity <= 0:
            self.fade_timer.stop()
            self.close()
        else:
            self.setWindowOpacity(self.opacity)


# ---------------------------------------------------------------------------
# FirebaseAuth  — autenticação + CRUD de templates/shortcuts no Firestore
# ---------------------------------------------------------------------------
class FirebaseAuth:
    def __init__(self):
        self.api_key    = FIREBASE_CONFIG['apiKey']
        self.project_id = FIREBASE_CONFIG['projectId']
        self.current_user = None
        self.id_token     = None
        self._cache_templates = {}  # chave: (field, value) → lista
        self._cache_shortcuts = {}  # chave: (field, value) → lista

    # ── helpers ──────────────────────────────────────────────────────────────
    def _base(self, collection, doc_id=''):
        path = f"projects/{self.project_id}/databases/(default)/documents/{collection}"
        if doc_id:
            path += f"/{doc_id}"
        return f"https://firestore.googleapis.com/v1/{path}"

    def _headers(self):
        return {"Authorization": f"Bearer {self.id_token}"}

    def _fields_to_dict(self, fields):
        result = {}
        for k, v in fields.items():
            if 'stringValue'  in v: result[k] = v['stringValue']
            elif 'booleanValue' in v: result[k] = v['booleanValue']
            elif 'integerValue' in v: result[k] = int(v['integerValue'])
            elif 'doubleValue'  in v: result[k] = v['doubleValue']
        return result

    # ── auth ─────────────────────────────────────────────────────────────────
    def signup(self, email, password, nome, setor):
        url  = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}"
        resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
        if resp.status_code == 200:
            result = resp.json()
            uid = result['localId']
            self.id_token = result['idToken']
            if self._is_first_user():
                self._upsert_usuario(uid, nome, setor, email, aprovado=True, is_admin=True)
                return {'success': True, 'uid': uid, 'first_admin': True}
            else:
                self._save_pending(uid, nome, setor, email)
                return {'success': True, 'uid': uid}
        return {'success': False, 'error': resp.json().get('error', {}).get('message', 'Erro')}

    def login(self, email, password):
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={self.api_key}"
        try:
            resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
            if resp.status_code == 200:
                result = resp.json()
                self.id_token = result['idToken']
                uid = result['localId']
                user_data = self.get_user_data(uid)
                if user_data and user_data.get('aprovado'):
                    self.current_user = user_data
                    self.current_user['uid'] = uid
                    return {'success': True, 'user': self.current_user}
                return {'success': False, 'error': 'Usuário aguardando aprovação'}
            return {'success': False, 'error': 'Email ou senha incorretos'}
        except Exception as e:
            return {'success': False, 'error': f'Erro de conexão: {str(e)}'}

    def logout(self):
        self.current_user = None
        self.id_token     = None

    def send_password_reset(self, email):
        url  = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={self.api_key}"
        resp = requests.post(url, json={"requestType": "PASSWORD_RESET", "email": email})
        return resp.status_code == 200

    # ── usuários ─────────────────────────────────────────────────────────────
    def _is_first_user(self):
        try:
            resp = requests.get(self._base('usuarios'), headers=self._headers())
            if resp.status_code == 200:
                return len(resp.json().get('documents', [])) == 0
        except:
            pass
        return True

    def _upsert_usuario(self, uid, nome, setor, email, aprovado=False, is_admin=False):
        data = {"fields": {
            "nome":     {"stringValue": nome},
            "setor":    {"stringValue": setor},
            "email":    {"stringValue": email},
            "aprovado": {"booleanValue": aprovado},
            "is_admin": {"booleanValue": is_admin},
        }}
        requests.patch(self._base('usuarios', uid), headers=self._headers(), json=data)

    def _save_pending(self, uid, nome, setor, email):
        data = {"fields": {
            "nome":  {"stringValue": nome},
            "setor": {"stringValue": setor},
            "email": {"stringValue": email},
        }}
        requests.patch(self._base('pending_users', uid), headers=self._headers(), json=data)

    def get_user_data(self, uid):
        resp = requests.get(self._base('usuarios', uid), headers=self._headers())
        if resp.status_code == 200:
            fields = resp.json().get('fields', {})
            return self._fields_to_dict(fields)
        return None

    def get_pending_users(self):
        resp = requests.get(self._base('pending_users'), headers=self._headers())
        if resp.status_code != 200:
            return []
        users = []
        for doc in resp.json().get('documents', []):
            uid = doc['name'].split('/')[-1]
            u = self._fields_to_dict(doc.get('fields', {}))
            u['uid'] = uid
            users.append(u)
        return users

    def get_approved_users(self):
        resp = requests.get(self._base('usuarios'), headers=self._headers())
        if resp.status_code != 200:
            return []
        users = []
        for doc in resp.json().get('documents', []):
            uid = doc['name'].split('/')[-1]
            u = self._fields_to_dict(doc.get('fields', {}))
            u['uid'] = uid
            users.append(u)
        return users

    def approve_user(self, uid, nome, setor, email):
        self._upsert_usuario(uid, nome, setor, email, aprovado=True, is_admin=False)
        requests.delete(self._base('pending_users', uid), headers=self._headers())
        return True

    def reject_user(self, uid):
        requests.delete(self._base('pending_users', uid), headers=self._headers())
        return True

    def promote_to_admin(self, uid):
        resp = requests.get(self._base('usuarios', uid), headers=self._headers())
        if resp.status_code == 200:
            fields = resp.json().get('fields', {})
            fields['is_admin'] = {"booleanValue": True}
            requests.patch(self._base('usuarios', uid), headers=self._headers(), json={"fields": fields})
            return True
        return False

    # ── templates no Firestore ───────────────────────────────────────────────
    # Estrutura: colecao "templates", cada doc tem: nome, texto, atalho, usuario_id, setor

    def _invalidate_cache(self):
        self._cache_templates = {}
        self._cache_shortcuts = {}

    def add_template(self, nome, texto, atalho, usuario_id, setor):
        data = {"fields": {
            "nome":       {"stringValue": nome},
            "texto":      {"stringValue": texto},
            "atalho":     {"stringValue": atalho or ""},
            "usuario_id": {"stringValue": usuario_id},
            "setor":      {"stringValue": setor},
        }}
        resp = requests.post(self._base('templates'), headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def update_template(self, doc_id, nome, texto, atalho):
        data = {"fields": {
            "nome":   {"stringValue": nome},
            "texto":  {"stringValue": texto},
            "atalho": {"stringValue": atalho or ""},
        }}
        resp = requests.patch(self._base('templates', doc_id), headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def delete_template(self, doc_id):
        resp = requests.delete(self._base('templates', doc_id), headers=self._headers())
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def get_templates_meus(self, usuario_id):
        key = ('usuario_id', usuario_id)
        if key not in self._cache_templates:
            self._cache_templates[key] = self._query_templates('usuario_id', usuario_id)
        return self._cache_templates[key]

    def get_templates_setor(self, setor):
        key = ('setor', setor)
        if key not in self._cache_templates:
            self._cache_templates[key] = self._query_templates('setor', setor)
        return self._cache_templates[key]

    def _query_templates(self, field, value):
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents:runQuery"
        body = {
            "structuredQuery": {
                "from": [{"collectionId": "templates"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": field},
                        "op":    "EQUAL",
                        "value": {"stringValue": value}
                    }
                }
            }
        }
        resp = requests.post(url, headers=self._headers(), json=body)
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json():
            doc = item.get('document')
            if not doc:
                continue
            doc_id = doc['name'].split('/')[-1]
            f = self._fields_to_dict(doc.get('fields', {}))
            results.append({
                'id':         doc_id,
                'nome':       f.get('nome', ''),
                'texto':      f.get('texto', ''),
                'atalho':     f.get('atalho', ''),
                'usuario_id': f.get('usuario_id', ''),
                'setor':      f.get('setor', ''),
            })
        return results

    def search_templates(self, query, setor=None):
        templates = self.get_templates_setor(setor) if setor else []
        q = query.lower()
        return [t for t in templates if q in t['nome'].lower() or q in t['texto'].lower()]

    # ── shortcuts no Firestore ───────────────────────────────────────────────
    def add_shortcut(self, nome, acoes, tecla_atalho, usuario_id, setor):
        data = {"fields": {
            "nome":         {"stringValue": nome},
            "acoes":        {"stringValue": json.dumps(acoes)},
            "tecla_atalho": {"stringValue": tecla_atalho or ""},
            "ativo":        {"booleanValue": True},
            "usuario_id":   {"stringValue": usuario_id},
            "setor":        {"stringValue": setor},
        }}
        resp = requests.post(self._base('shortcuts'), headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def update_shortcut(self, doc_id, nome, acoes, tecla_atalho, usuario_id, setor):
        data = {"fields": {
            "nome":         {"stringValue": nome},
            "acoes":        {"stringValue": json.dumps(acoes)},
            "tecla_atalho": {"stringValue": tecla_atalho or ""},
            "usuario_id":   {"stringValue": usuario_id},
            "setor":        {"stringValue": setor},
        }}
        resp = requests.patch(self._base('shortcuts', doc_id), headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def delete_shortcut(self, doc_id):
        resp = requests.delete(self._base('shortcuts', doc_id), headers=self._headers())
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def toggle_shortcut(self, doc_id, ativo_atual):
        resp = requests.get(self._base('shortcuts', doc_id), headers=self._headers())
        if resp.status_code == 200:
            fields = resp.json().get('fields', {})
            fields['ativo'] = {"booleanValue": not ativo_atual}
            requests.patch(self._base('shortcuts', doc_id), headers=self._headers(), json={"fields": fields})
            self._invalidate_cache()

    def get_shortcuts_meus(self, usuario_id):
        key = ('usuario_id', usuario_id)
        if key not in self._cache_shortcuts:
            self._cache_shortcuts[key] = self._query_shortcuts('usuario_id', usuario_id)
        return self._cache_shortcuts[key]

    def get_shortcuts_setor(self, setor):
        key = ('setor', setor)
        if key not in self._cache_shortcuts:
            self._cache_shortcuts[key] = self._query_shortcuts('setor', setor)
        return self._cache_shortcuts[key]

    def _query_shortcuts(self, field, value):
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents:runQuery"
        body = {
            "structuredQuery": {
                "from": [{"collectionId": "shortcuts"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": field},
                        "op":    "EQUAL",
                        "value": {"stringValue": value}
                    }
                }
            }
        }
        resp = requests.post(url, headers=self._headers(), json=body)
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json():
            doc = item.get('document')
            if not doc:
                continue
            doc_id = doc['name'].split('/')[-1]
            f = self._fields_to_dict(doc.get('fields', {}))
            try:
                acoes = json.loads(f.get('acoes', '[]'))
            except:
                acoes = []
            results.append({
                'id':           doc_id,
                'nome':         f.get('nome', ''),
                'ativo':        f.get('ativo', True),
                'acoes':        acoes,
                'tecla_atalho': f.get('tecla_atalho', ''),
                'usuario_id':   f.get('usuario_id', ''),
                'setor':        f.get('setor', ''),
            })
        return results

    # config simples (salvo localmente via arquivo json pequeno)
    def get_config(self, chave, default=None):
        try:
            with open('at_config.json', 'r') as f:
                return json.load(f).get(chave, default)
        except:
            return default

    def set_config(self, chave, valor):
        try:
            try:
                with open('at_config.json', 'r') as f:
                    cfg = json.load(f)
            except:
                cfg = {}
            cfg[chave] = valor
            with open('at_config.json', 'w') as f:
                json.dump(cfg, f)
        except:
            pass


# ---------------------------------------------------------------------------
# LoginWindow
# ---------------------------------------------------------------------------
class LoginWindow(QWidget):
    login_success = pyqtSignal(dict)

    def __init__(self, firebase):
        super().__init__()
        self.firebase = firebase
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('AssistiveTouch - Login')
        self.setFixedWidth(400)
        self.setWindowFlags(Qt.WindowType.Window)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 20, 40, 40)
        layout.setSpacing(10)

        titulo = QLabel('🔘 AssistiveTouch')
        titulo.setStyleSheet('font-size: 24px; font-weight: bold; color: #2d2d2d;')
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(titulo)

        subtitulo = QLabel('Sistema de Automação Multi-usuário')
        subtitulo.setStyleSheet('font-size: 12px; color: #666;')
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitulo)

        layout.addSpacing(10)

        self.tabs = QTabWidget()

        # ── Tab Login ────────────────────────────────────────────────────────
        login_tab = QWidget()
        ll = QVBoxLayout()

        ll.addWidget(QLabel('Email:'))
        self.login_email = QLineEdit()
        self.login_email.setPlaceholderText('seu@email.com')
        self.login_email.returnPressed.connect(self.do_login)
        ll.addWidget(self.login_email)

        ll.addWidget(QLabel('Senha:'))
        self.login_senha = QLineEdit()
        self.login_senha.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_senha.setPlaceholderText('••••••••')
        self.login_senha.returnPressed.connect(self.do_login)
        ll.addWidget(self.login_senha)

        btn_login = QPushButton('Entrar')
        btn_login.setStyleSheet("""
            QPushButton { background-color:#406e54; color:white; padding:12px; border-radius:6px; font-weight:bold; }
            QPushButton:hover { background-color:#355a45; }
        """)
        btn_login.clicked.connect(self.do_login)
        btn_login.setDefault(True)
        ll.addWidget(btn_login)

        esqueci = QLabel('<a href="#" style="color:#406e54;">Esqueci minha senha</a>')
        esqueci.setAlignment(Qt.AlignmentFlag.AlignCenter)
        esqueci.linkActivated.connect(self.esqueci_senha)
        ll.addWidget(esqueci)

        login_tab.setLayout(ll)

        # ── Tab Cadastro ─────────────────────────────────────────────────────
        cad_tab = QWidget()
        cl = QVBoxLayout()

        cl.addWidget(QLabel('Nome Completo:'))
        self.cad_nome = QLineEdit()
        cl.addWidget(self.cad_nome)

        cl.addWidget(QLabel('Email:'))
        self.cad_email = QLineEdit()
        self.cad_email.setPlaceholderText('seu@email.com')
        cl.addWidget(self.cad_email)

        cl.addWidget(QLabel('Senha:'))
        self.cad_senha = QLineEdit()
        self.cad_senha.setEchoMode(QLineEdit.EchoMode.Password)
        cl.addWidget(self.cad_senha)

        cl.addWidget(QLabel('Confirmar Senha:'))
        self.cad_senha_confirm = QLineEdit()
        self.cad_senha_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        cl.addWidget(self.cad_senha_confirm)

        cl.addWidget(QLabel('Setor:'))
        self.cad_setor = QComboBox()
        self.cad_setor.addItems(SETORES)
        cl.addWidget(self.cad_setor)

        btn_cad = QPushButton('Criar Conta')
        btn_cad.setStyleSheet("""
            QPushButton { background-color:#88c22b; color:white; padding:12px; border-radius:6px; font-weight:bold; font-size:13px; }
            QPushButton:hover { background-color:#76a824; }
        """)
        btn_cad.clicked.connect(self.do_cadastro)
        cl.addWidget(btn_cad)

        cad_tab.setLayout(cl)

        self.tabs.addTab(login_tab, 'Login')
        self.tabs.addTab(cad_tab, 'Cadastro')
        self.tabs.currentChanged.connect(lambda idx: self.setFixedHeight(380 if idx == 0 else 520))
        layout.addWidget(self.tabs)

        self.setLayout(layout)
        self.setFixedHeight(350)

    def do_login(self):
        email = self.login_email.text().strip()
        senha = self.login_senha.text()
        if not email or not senha:
            QMessageBox.warning(self, 'Erro', 'Preencha todos os campos!')
            return
        result = self.firebase.login(email, senha)
        if result['success']:
            self.login_success.emit(result['user'])
            self.close()
        else:
            QMessageBox.warning(self, 'Erro', result['error'])

    def do_cadastro(self):
        nome  = self.cad_nome.text().strip()
        email = self.cad_email.text().strip()
        senha = self.cad_senha.text()
        conf  = self.cad_senha_confirm.text()
        setor = self.cad_setor.currentText()

        if not all([nome, email, senha, conf]):
            QMessageBox.warning(self, 'Erro', 'Preencha todos os campos!')
            return
        if senha != conf:
            QMessageBox.warning(self, 'Erro', 'As senhas não coincidem!')
            return

        result = self.firebase.signup(email, senha, nome, setor)
        if result['success']:
            if result.get('first_admin'):
                QMessageBox.information(self, 'Admin Criado!',
                    'Você é o primeiro usuário e foi configurado como administrador.\n\nFaça login para começar.')
            else:
                QMessageBox.information(self, 'Cadastro Enviado',
                    'Conta criada! Aguarde aprovação do administrador.')
            self.tabs.setCurrentIndex(0)
            self.cad_nome.clear(); self.cad_email.clear()
            self.cad_senha.clear(); self.cad_senha_confirm.clear()
        else:
            QMessageBox.warning(self, 'Erro', result['error'])

    def esqueci_senha(self):
        email = self.login_email.text().strip()
        if not email:
            QMessageBox.warning(self, 'Erro', 'Digite seu email primeiro!')
            return
        if self.firebase.send_password_reset(email):
            QMessageBox.information(self, 'Email Enviado',
                'Um email de recuperação foi enviado.\n\nVerifique sua caixa de entrada.')
        else:
            QMessageBox.warning(self, 'Erro', 'Email não encontrado!')


# ---------------------------------------------------------------------------
# KeyboardSignals / KeyboardListener
# ---------------------------------------------------------------------------
class KeyboardSignals(QObject):
    show_popup   = pyqtSignal(int, int)
    update_popup = pyqtSignal(str)
    close_popup  = pyqtSignal()
    insert_text  = pyqtSignal(str, int)


class KeyboardListener:
    def __init__(self, firebase, user_data):
        self.firebase    = firebase
        self.user_data   = user_data
        self.typed_text  = ""
        self.keyboard_controller = KeyboardController()
        self.mouse_controller    = MouseController()
        self.listener        = None
        self.templates_popup = None
        self.search_mode  = False
        self.search_query = ""
        self.signals      = KeyboardSignals()
        self.alt_pressed  = False

        self.signals.show_popup.connect(self._show_popup_slot)
        self.signals.update_popup.connect(self._update_popup_slot)
        self.signals.close_popup.connect(self._close_popup_slot)
        self.signals.insert_text.connect(self._insert_text_slot)

    def start(self):
        self.listener = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self.listener.start()

    def on_key_press(self, key):
        try:
            if key in (Key.alt_l, Key.alt_r, Key.alt):
                self.alt_pressed = True
                return

            if self.search_mode:
                if key in (Key.right, Key.enter):
                    if self.templates_popup:
                        item = self.templates_popup.list_widget.currentItem()
                        if item:
                            texto = item.data(Qt.ItemDataRole.UserRole)
                            if texto:
                                self.signals.insert_text.emit(texto, 2 + len(self.search_query))
                elif key in (Key.esc, Key.space):
                    self.cancel_search()
                elif key == Key.backspace:
                    if self.search_query:
                        self.search_query = self.search_query[:-1]
                        self.signals.update_popup.emit(self.search_query)
                    else:
                        self.cancel_search()
                elif key == Key.up:
                    if self.templates_popup: self.templates_popup.select_previous()
                elif key == Key.down:
                    if self.templates_popup: self.templates_popup.select_next()
                elif hasattr(key, 'char') and key.char:
                    self.search_query += key.char
                    self.signals.update_popup.emit(self.search_query)
                return

            if self.alt_pressed and hasattr(key, 'char') and key.char:
                self.check_alt_shortcuts(key.char.upper())
                return

            if hasattr(key, 'char') and key.char:
                self.typed_text += key.char
                if self.typed_text.endswith('//'):
                    pos = QCursor.pos()
                    self.signals.show_popup.emit(pos.x(), pos.y())
                    return
                if len(self.typed_text) > 30:
                    self.typed_text = self.typed_text[-30:]
            elif key == Key.space:
                self.check_text_shortcuts()
                self.typed_text = ""
            elif key in (Key.enter, Key.tab):
                self.typed_text = ""
        except Exception as e:
            print(f"Erro no listener: {e}")

    def on_key_release(self, key):
        if key in (Key.alt_l, Key.alt_r, Key.alt):
            self.alt_pressed = False

    def _show_popup_slot(self, x, y):
        self.search_mode  = True
        self.search_query = ""
        if self.templates_popup:
            try: self.templates_popup.close()
            except: pass

        self.templates_popup = TemplatesPopup(self.firebase, self.user_data, self)
        screen = QApplication.primaryScreen().geometry()
        px = min(x + 10, screen.width() - self.templates_popup.width() - 10)
        py = y - self.templates_popup.height() - 5
        if py < 10: py = y + 25
        self.templates_popup.move(max(px, 10), py)
        self.templates_popup.show()
        self.templates_popup.raise_()

    def _update_popup_slot(self, query):
        if self.templates_popup:
            self.templates_popup.update_search(query)

    def _close_popup_slot(self):
        if self.templates_popup:
            self.templates_popup.close()
            self.templates_popup = None

    def _insert_text_slot(self, texto, chars_to_delete):
        self.search_mode  = False
        self.search_query = ""
        self.typed_text   = ""
        if self.templates_popup:
            try: self.templates_popup.close()
            except: pass
            self.templates_popup = None

        def digitar():
            try:
                time.sleep(0.05)
                for _ in range(chars_to_delete):
                    self.keyboard_controller.press(Key.backspace)
                    self.keyboard_controller.release(Key.backspace)
                    time.sleep(0.003)
                time.sleep(0.05)
                linhas = texto.split('\n')
                for i, linha in enumerate(linhas):
                    if linha:
                        self.keyboard_controller.type(linha)
                    if i < len(linhas) - 1 or texto.endswith('\n'):
                        with self.keyboard_controller.pressed(Key.shift):
                            self.keyboard_controller.press(Key.enter)
                            self.keyboard_controller.release(Key.enter)
                        time.sleep(0.05)
            except Exception as e:
                print(f"Erro ao digitar: {e}")

        t = threading.Thread(target=digitar, daemon=True)
        t.start()

    def cancel_search(self):
        self.search_mode  = False
        self.search_query = ""
        self.typed_text   = ""
        self.signals.close_popup.emit()

    def check_text_shortcuts(self):
        if not self.typed_text.strip():
            return
        uid   = self.user_data['uid']
        setor = self.user_data['setor']

        # Verificar templates
        for t in self.firebase.get_templates_setor(setor):
            if t['atalho'] and t['atalho'].lower() == self.typed_text.strip().lower():
                self._apagar_e_digitar(t['texto'], len(self.typed_text) + 1)
                return

        # Verificar shortcuts
        for s in self.firebase.get_shortcuts_setor(setor):
            if not s['ativo']: continue
            tecla = s.get('tecla_atalho', '')
            if len(tecla) > 2 and tecla.lower() == self.typed_text.strip().lower():
                for _ in range(len(self.typed_text) + 1):
                    self.keyboard_controller.press(Key.backspace)
                    self.keyboard_controller.release(Key.backspace)
                    time.sleep(0.01)
                self.execute_shortcut(s['acoes'])
                return

    def check_alt_shortcuts(self, char):
        setor = self.user_data['setor']
        for s in self.firebase.get_shortcuts_setor(setor):
            if not s['ativo']: continue
            tecla = s.get('tecla_atalho', '')
            if len(tecla) <= 2 and tecla.upper() == char.upper():
                def run():
                    time.sleep(0.1)
                    self.execute_shortcut(s['acoes'])
                threading.Thread(target=run, daemon=True).start()
                return

    def _apagar_e_digitar(self, texto, n_backspaces):
        def run():
            for _ in range(n_backspaces):
                self.keyboard_controller.press(Key.backspace)
                self.keyboard_controller.release(Key.backspace)
                time.sleep(0.01)
            time.sleep(0.05)
            linhas = texto.split('\n')
            for i, linha in enumerate(linhas):
                if linha:
                    self.keyboard_controller.type(linha)
                if i < len(linhas) - 1 or texto.endswith('\n'):
                    with self.keyboard_controller.pressed(Key.shift):
                        self.keyboard_controller.press(Key.enter)
                        self.keyboard_controller.release(Key.enter)
                    time.sleep(0.05)
        threading.Thread(target=run, daemon=True).start()

    def execute_shortcut(self, acoes):
        def run():
            try:
                time.sleep(0.1)
                for acao in acoes:
                    if acao['type'] == 'click':
                        vezes = acao.get('vezes', 1)
                        self.mouse_controller.position = (acao['x'], acao['y'])
                        for _ in range(vezes):
                            self.mouse_controller.click(Button.left, 1)
                            if vezes > 1: time.sleep(0.1)
                    elif acao['type'] == 'right_click':
                        self.mouse_controller.position = (acao['x'], acao['y'])
                        self.mouse_controller.click(Button.right, 1)
                    elif acao['type'] == 'drag':
                        self.mouse_controller.position = (acao['x1'], acao['y1'])
                        time.sleep(0.1)
                        self.mouse_controller.press(Button.left)
                        time.sleep(0.05)
                        self.mouse_controller.position = (acao['x2'], acao['y2'])
                        time.sleep(0.1)
                        self.mouse_controller.release(Button.left)
                    elif acao['type'] == 'type':
                        self._apagar_e_digitar(acao['text'], 0)
                        time.sleep(0.1 + len(acao['text']) * 0.01)
                    elif acao['type'] == 'sleep':
                        time.sleep(acao['ms'] / 1000.0)
                    time.sleep(0.05)
            except Exception as e:
                print(f"Erro ao executar ações: {e}")
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# TemplatesPopup
# ---------------------------------------------------------------------------
class TemplatesPopup(QWidget):
    def __init__(self, firebase, user_data, listener):
        super().__init__()
        self.firebase  = firebase
        self.user_data = user_data
        self.listener  = listener
        self.current_templates = []
        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        container = QWidget()
        container.setStyleSheet("QWidget { background-color:white; border:2px solid #ddd; border-radius:8px; }")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(10,10,10,10); lay.setSpacing(5)

        self.title_label = QLabel('// (digite para buscar)')
        self.title_label.setStyleSheet("QLabel { color:#999; font-size:11px; padding:5px; background:transparent; }")
        lay.addWidget(self.title_label)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget { border:none; background:white; outline:none; font-size:12px; }
            QListWidget::item { padding:10px; border-radius:4px; margin:2px 0px; color:#333; background-color:white; }
            QListWidget::item:selected { background-color:#2196F3; color:white; }
            QListWidget::item:hover { background-color:#E3F2FD; color:#333; }
        """)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        lay.addWidget(self.list_widget)

        info = QLabel('→ Seta direita para inserir  |  ESC para cancelar')
        info.setStyleSheet("QLabel { color:#999; font-size:10px; padding:5px; background:transparent; }")
        lay.addWidget(info)

        ml = QVBoxLayout(self)
        ml.setContentsMargins(0,0,0,0)
        ml.addWidget(container)

        self.setFixedWidth(400)
        self.setFixedHeight(300)
        self.update_search("")

    def update_search(self, query):
        self.list_widget.clear()
        setor = self.user_data['setor']
        if query:
            self.title_label.setText(f'// {query}')
            self.current_templates = self.firebase.search_templates(query, setor=setor)
        else:
            self.title_label.setText('// (digite para buscar)')
            self.current_templates = self.firebase.get_templates_setor(setor)

        if self.current_templates:
            for t in self.current_templates:
                item = QListWidgetItem()
                preview = t['texto'][:50] + '...' if len(t['texto']) > 50 else t['texto']
                item.setText(f"{t['nome']}\n{preview}")
                item.setData(Qt.ItemDataRole.UserRole, t['texto'])
                self.list_widget.addItem(item)
            self.list_widget.setCurrentRow(0)
        else:
            self.list_widget.addItem(QListWidgetItem("Nenhum template encontrado"))

    def select_next(self):
        cur = self.list_widget.currentRow()
        self.list_widget.setCurrentRow(min(cur + 1, self.list_widget.count() - 1))

    def select_previous(self):
        cur = self.list_widget.currentRow()
        self.list_widget.setCurrentRow(max(cur - 1, 0))

    def on_item_clicked(self, item):
        texto = item.data(Qt.ItemDataRole.UserRole)
        if texto:
            chars = 2 + len(self.listener.search_query)
            self.listener.search_mode  = False
            self.listener.search_query = ""
            self.listener.typed_text   = ""
            self.close()
            QTimer.singleShot(50, lambda: self.listener.signals.insert_text.emit(texto, chars))


# ---------------------------------------------------------------------------
# FloatingCircle
# ---------------------------------------------------------------------------
class FloatingCircle(QWidget):
    def __init__(self, firebase, user_data):
        super().__init__()
        self.firebase   = firebase
        self.user_data  = user_data
        self.dragging   = False
        self.drag_start_position = QPoint()
        self.click_position      = QPoint()
        self.menu      = None
        self.menu_open = False
        self._scale         = 1.0
        self._opacity_value = 1.0
        self.init_ui()

        self.scale_animation = QPropertyAnimation(self, b"scale")
        self.scale_animation.setDuration(200)
        self.scale_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.opacity_animation = QPropertyAnimation(self, b"opacity_value")
        self.opacity_animation.setDuration(200)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        self._scale = value
        self.update()

    @pyqtProperty(float)
    def opacity_value(self):
        return self._opacity_value

    @opacity_value.setter
    def opacity_value(self, value):
        self._opacity_value = value
        self.setWindowOpacity(value)

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(80, 80)
        self.setWindowOpacity(1.0)
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - 120, 50)
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        painter.translate(cx, cy)
        painter.scale(self._scale, self._scale)
        painter.translate(-cx, -cy)

        gradient = QRadialGradient(cx, cy, 40)
        gradient.setColorAt(0, QColor(60, 60, 60, 150))
        gradient.setColorAt(0.7, QColor(40, 40, 40, 100))
        gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 80, 80)

        painter.setPen(QPen(QColor(80, 80, 80), 3));  painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawEllipse(8,  8, 64, 64)
        painter.setPen(QPen(QColor(120,120,120), 2.5));                                          painter.drawEllipse(12, 12, 56, 56)
        painter.setPen(QPen(QColor(160,160,160), 2));                                            painter.drawEllipse(16, 16, 48, 48)

        gc = QRadialGradient(cx, cy, 21)
        gc.setColorAt(0, QColor(255,255,255,255))
        gc.setColorAt(0.8, QColor(240,240,240,255))
        gc.setColorAt(1, QColor(220,220,220,255))
        painter.setBrush(gc)
        painter.setPen(QPen(QColor(180,180,180), 1))
        painter.drawEllipse(20, 20, 40, 40)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.click_position      = event.globalPosition().toPoint()
            self.drag_start_position = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - self.click_position).manhattanLength() > 5:
                self.dragging = True
                self.move(event.globalPosition().toPoint() - self.drag_start_position)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.dragging:
                self.show_menu()
            self.dragging = False

    def _animate_circle(self, scale_end, opacity_end):
        self.scale_animation.stop()
        self.scale_animation.setStartValue(self._scale)
        self.scale_animation.setEndValue(scale_end)
        self.scale_animation.start()
        self.opacity_animation.stop()
        self.opacity_animation.setStartValue(self._opacity_value)
        self.opacity_animation.setEndValue(opacity_end)
        self.opacity_animation.start()

    def show_menu(self):
        if self.menu and self.menu.isVisible():
            # fechar menu com animação
            try:
                cp = QPoint(self.x(), self.y())
                self.menu_close_slide = QPropertyAnimation(self.menu, b"pos")
                self.menu_close_slide.setDuration(200)
                self.menu_close_slide.setStartValue(self.menu.pos())
                self.menu_close_slide.setEndValue(cp)
                self.menu_close_slide.setEasingCurve(QEasingCurve.Type.InCubic)

                self.menu_close_fade = QPropertyAnimation(self.menu, b"windowOpacity")
                self.menu_close_fade.setDuration(200)
                self.menu_close_fade.setStartValue(1.0)
                self.menu_close_fade.setEndValue(0.0)

                def on_close_done():
                    if self.menu: self.menu.close(); self.menu = None
                    self.menu_open = False

                self.menu_close_fade.finished.connect(on_close_done)
                self.menu_close_slide.start()
                self.menu_close_fade.start()
            except:
                if self.menu: self.menu.close(); self.menu = None
                self.menu_open = False

            self._animate_circle(0.85, 0.6)
            return

        self.menu_open = True
        self.menu = MainMenu(self.firebase, self.user_data, self)

        def on_closed():
            self.menu = None
            self.menu_open = False

        self.menu.destroyed.connect(on_closed)

        mx = self.x() - 450
        my = self.y()
        cp = QPoint(self.x(), self.y())
        fp = QPoint(mx, my)

        try:
            self.menu.move(cp)
            self.menu.setWindowOpacity(0.0)
            self.menu.show()
            self.menu.raise_()

            self.menu_slide = QPropertyAnimation(self.menu, b"pos")
            self.menu_slide.setDuration(250)
            self.menu_slide.setStartValue(cp)
            self.menu_slide.setEndValue(fp)
            self.menu_slide.setEasingCurve(QEasingCurve.Type.OutCubic)

            self.menu_fade = QPropertyAnimation(self.menu, b"windowOpacity")
            self.menu_fade.setDuration(250)
            self.menu_fade.setStartValue(0.0)
            self.menu_fade.setEndValue(1.0)

            self.menu_slide.start()
            self.menu_fade.start()
        except:
            self.menu.move(mx, my)
            self.menu.setWindowOpacity(1.0)
            self.menu.show()
            self.menu.raise_()

        self._animate_circle(1.0, 1.0)

    def enterEvent(self, event):
        if not self.menu_open:
            self._animate_circle(1.0, 1.0)

    def leaveEvent(self, event):
        if not self.menu_open:
            self._animate_circle(0.85, 0.6)


# ---------------------------------------------------------------------------
# helpers de ícones SVG
# ---------------------------------------------------------------------------
def create_svg_icon(svg_code, size=20):
    svg_bytes = QByteArray(svg_code.encode())
    renderer  = QSvgRenderer(svg_bytes)
    pixmap    = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter   = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# OverlayDialog  (blur overlay para criação de templates)
# ---------------------------------------------------------------------------
class OverlayDialog(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        if parent:
            self.setGeometry(0, 0, parent.width(), parent.height())
        self.background_pixmap = parent.grab() if parent else None

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.content_card = QWidget()
        self.content_card.setFixedSize(410, 450)
        self.content_card.setStyleSheet("QWidget { background-color:white; border-radius:15px; }")

        self.card_layout = QVBoxLayout()
        self.card_layout.setContentsMargins(20, 20, 20, 20)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_card.setLayout(self.card_layout)

        layout.addWidget(self.content_card)
        self.setLayout(layout)
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.background_pixmap:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            self.background_pixmap.save(buf, 'PNG')
            buf.close()
            pil = Image.open(BytesIO(ba.data()))
            blurred = pil.filter(ImageFilter.GaussianBlur(radius=5))
            bb = BytesIO(); blurred.save(bb, 'PNG'); bb.seek(0)
            bp = QPixmap(); bp.loadFromData(bb.getvalue())
            path = QPainterPath()
            path.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, bp)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        super().paintEvent(event)

    def add_content(self, widget):
        self.card_layout.addWidget(widget)

    def mousePressEvent(self, event):
        card_pos  = self.content_card.mapToParent(QPoint(0, 0))
        card_rect = QRect(card_pos, self.content_card.size())
        if not card_rect.contains(event.pos()):
            self.close()
        else:
            event.ignore()


# ---------------------------------------------------------------------------
# MainMenu
# ---------------------------------------------------------------------------
class MainMenu(QWidget):
    _last_tab             = 'templates'
    _last_sub_tab_templates = 'meus'
    _last_sub_tab_atalhos   = 'meus'

    def __init__(self, firebase, user_data, parent=None):
        super().__init__(parent)
        self.firebase    = firebase
        self.user_data   = user_data
        self.circle_parent = parent
        self.add_window  = None
        self.init_ui()

    # ── fechamento ────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._reset_circle()
        event.accept()

    def hideEvent(self, event):
        self._reset_circle()
        event.accept()

    def _reset_circle(self):
        if self.circle_parent:
            self.circle_parent.menu_open = False
            self.circle_parent._animate_circle(0.85, 0.6)

    # ── UI ───────────────────────────────────────────────────────────────────
    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Popup
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(450, 520)

        container = QWidget()
        container.setStyleSheet("QWidget { background-color:#DEDDD2; border-radius:10px; }")
        cl = QVBoxLayout()
        cl.setContentsMargins(15, 15, 15, 15)
        cl.setSpacing(10)

        # abas principais
        tabs_layout = QHBoxLayout(); tabs_layout.setSpacing(10)
        self.btn_templates = QPushButton('Templates')
        self.btn_atalhos   = QPushButton('Atalhos')
        font = QFont("Instrument Sans", 14)
        self.btn_templates.setFont(font); self.btn_atalhos.setFont(font)
        self.btn_templates.clicked.connect(self.show_templates_tab)
        self.btn_atalhos.clicked.connect(self.show_atalhos_tab)
        tabs_layout.addWidget(self.btn_templates); tabs_layout.addWidget(self.btn_atalhos)
        cl.addLayout(tabs_layout)

        # sub-abas
        self.sub_tabs_layout = QHBoxLayout()
        self.sub_tabs_layout.setSpacing(20)
        self.sub_tabs_layout.setContentsMargins(0, 5, 0, 10)

        self.btn_meus  = QPushButton('Meus templates')
        self.btn_setor = QPushButton('Templates do setor')
        font_sub = QFont("Inter", 11)
        self.btn_meus.setFont(font_sub); self.btn_setor.setFont(font_sub)
        self.btn_meus.clicked.connect(lambda: self.on_sub_tab_click('meus'))
        self.btn_setor.clicked.connect(lambda: self.on_sub_tab_click('setor'))
        self.sub_tabs_layout.addStretch()
        self.sub_tabs_layout.addWidget(self.btn_meus)
        self.sub_tabs_layout.addWidget(self.btn_setor)
        self.sub_tabs_layout.addStretch()
        cl.addLayout(self.sub_tabs_layout)

        # área de conteúdo com scroll
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self.content_area = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.content_area.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_area)
        cl.addWidget(self.scroll_area)

        cl.addStretch()

        # rodapé
        footer = QHBoxLayout(); footer.setContentsMargins(0, 5, 0, 0); footer.setSpacing(10)

        svg_search = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M17.5 17.5L13.875 13.875M15.8333 9.16667C15.8333 12.8486 12.8486 15.8333 9.16667 15.8333C5.48477 15.8333 2.5 12.8486 2.5 9.16667C2.5 5.48477 5.48477 2.5 9.16667 2.5C12.8486 2.5 15.8333 5.48477 15.8333 9.16667Z" stroke="#1E1E1E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

        svg_add_t = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect width="20" height="20" rx="3" fill="#B97E88"/>
<path d="M10 5.33331V14.6666M5.33337 9.99998H14.6667" stroke="white" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

        svg_add_a = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect width="20" height="20" rx="3" fill="#499714"/>
<path d="M10 5.33331V14.6666M5.33337 9.99998H14.6667" stroke="white" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

        svg_config = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<g clip-path="url(#clip0_13_57)">
<path d="M10 12.5C11.3808 12.5 12.5 11.3807 12.5 9.99998C12.5 8.61927 11.3808 7.49998 10 7.49998C8.61933 7.49998 7.50004 8.61927 7.50004 9.99998C7.50004 11.3807 8.61933 12.5 10 12.5Z" stroke="#1E1E1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M16.1667 12.5C16.0558 12.7513 16.0227 13.0301 16.0717 13.3005C16.1207 13.5708 16.2496 13.8202 16.4417 14.0166L16.4917 14.0666C16.6467 14.2214 16.7696 14.4052 16.8535 14.6076C16.9373 14.8099 16.9805 15.0268 16.9805 15.2458C16.9805 15.4648 16.9373 15.6817 16.8535 15.884C16.7696 16.0864 16.6467 16.2702 16.4917 16.425C16.3369 16.5799 16.1531 16.7029 15.9508 16.7867C15.7484 16.8706 15.5316 16.9138 15.3125 16.9138C15.0935 16.9138 14.8766 16.8706 14.6743 16.7867C14.472 16.7029 14.2882 16.5799 14.1334 16.425L14.0834 16.375C13.887 16.1829 13.6375 16.054 13.3672 16.005C13.0969 15.956 12.8181 15.989 12.5667 16.1C12.3202 16.2056 12.11 16.381 11.962 16.6046C11.8139 16.8282 11.7344 17.0902 11.7334 17.3583V17.5C11.7334 17.942 11.5578 18.3659 11.2452 18.6785C10.9327 18.9911 10.5087 19.1666 10.0667 19.1666C9.62468 19.1666 9.20076 18.9911 8.8882 18.6785C8.57564 18.3659 8.40004 17.942 8.40004 17.5V17.425C8.39359 17.1491 8.30431 16.8816 8.1438 16.6572C7.98329 16.4328 7.75899 16.2619 7.50004 16.1666C7.24869 16.0557 6.96988 16.0226 6.69955 16.0716C6.42922 16.1207 6.17977 16.2495 5.98337 16.4416L5.93337 16.4916C5.77859 16.6466 5.59477 16.7695 5.39244 16.8534C5.19011 16.9373 4.97323 16.9805 4.75421 16.9805C4.53518 16.9805 4.3183 16.9373 4.11597 16.8534C3.91364 16.7695 3.72983 16.6466 3.57504 16.4916C3.42008 16.3369 3.29715 16.153 3.21327 15.9507C3.1294 15.7484 3.08623 15.5315 3.08623 15.3125C3.08623 15.0935 3.1294 14.8766 3.21327 14.6742C3.29715 14.4719 3.42008 14.2881 3.57504 14.1333L3.62504 14.0833C3.81715 13.8869 3.94603 13.6375 3.99504 13.3671C4.04406 13.0968 4.01097 12.818 3.90004 12.5666C3.7944 12.3202 3.619 12.11 3.39543 11.9619C3.17185 11.8138 2.90986 11.7344 2.64171 11.7333H2.50004C2.05801 11.7333 1.63409 11.5577 1.32153 11.2452C1.00897 10.9326 0.833374 10.5087 0.833374 10.0666C0.833374 9.62462 1.00897 9.2007 1.32153 8.88813C1.63409 8.57557 2.05801 8.39998 2.50004 8.39998H2.57504C2.85087 8.39353 3.11838 8.30424 3.34279 8.14374C3.5672 7.98323 3.73814 7.75893 3.83337 7.49998C3.9443 7.24863 3.97739 6.96982 3.92838 6.69949C3.87936 6.42916 3.75049 6.17971 3.55837 5.98331L3.50837 5.93331C3.35341 5.77852 3.23048 5.59471 3.14661 5.39238C3.06273 5.19005 3.01956 4.97317 3.01956 4.75415C3.01956 4.53512 3.06273 4.31824 3.14661 4.11591C3.23048 3.91358 3.35341 3.72977 3.50837 3.57498C3.66316 3.42002 3.84698 3.29709 4.04931 3.21321C4.25164 3.12934 4.46851 3.08617 4.68754 3.08617C4.90657 3.08617 5.12344 3.12934 5.32577 3.21321C5.5281 3.29709 5.71192 3.42002 5.86671 3.57498L5.91671 3.62498C6.11311 3.81709 6.36255 3.94597 6.63288 3.99498C6.90321 4.044 7.18203 4.01091 7.43337 3.89998H7.50004C7.74651 3.79434 7.95672 3.61894 8.10478 3.39537C8.25285 3.17179 8.3323 2.9098 8.33337 2.64165V2.49998C8.33337 2.05795 8.50897 1.63403 8.82153 1.32147C9.13409 1.00891 9.55801 0.833313 10 0.833313C10.4421 0.833313 10.866 1.00891 11.1786 1.32147C11.4911 1.63403 11.6667 2.05795 11.6667 2.49998V2.57498C11.6678 2.84313 11.7472 3.10513 11.8953 3.3287C12.0434 3.55228 12.2536 3.72768 12.5 3.83331C12.7514 3.94424 13.0302 3.97733 13.3005 3.92832C13.5709 3.8793 13.8203 3.75043 14.0167 3.55831L14.0667 3.50831C14.2215 3.35335 14.4053 3.23042 14.6076 3.14655C14.81 3.06267 15.0268 3.0195 15.2459 3.0195C15.4649 3.0195 15.6818 3.06267 15.8841 3.14655C16.0864 3.23042 16.2702 3.35335 16.425 3.50831C16.58 3.6631 16.7029 3.84692 16.7868 4.04925C16.8707 4.25158 16.9139 4.46845 16.9139 4.68748C16.9139 4.90651 16.8707 5.12338 16.7868 5.32571C16.7029 5.52804 16.58 5.71186 16.425 5.86665L16.375 5.91665C16.1829 6.11304 16.0541 6.36249 16.005 6.63282C15.956 6.90315 15.9891 7.18197 16.1 7.43331V7.49998C16.2057 7.74645 16.3811 7.95666 16.6047 8.10472C16.8282 8.25279 17.0902 8.33224 17.3584 8.33331H17.5C17.9421 8.33331 18.366 8.50891 18.6786 8.82147C18.9911 9.13403 19.1667 9.55795 19.1667 9.99998C19.1667 10.442 18.9911 10.8659 18.6786 11.1785C18.366 11.4911 17.9421 11.6666 17.5 11.6666H17.425C17.1569 11.6677 16.8949 11.7472 16.6713 11.8952C16.4477 12.0433 16.2723 12.2535 16.1667 12.5Z" stroke="#1E1E1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
</g><defs><clipPath id="clip0_13_57"><rect width="20" height="20" fill="white"/></clipPath></defs></svg>"""

        svg_sair = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M7.5 17.5H4.16667C3.72464 17.5 3.30072 17.3244 2.98816 17.0118C2.67559 16.6993 2.5 16.2754 2.5 15.8333V4.16667C2.5 3.72464 2.67559 3.30072 2.98816 2.98816C3.30072 2.67559 3.72464 2.5 4.16667 2.5H7.5M13.3333 14.1667L17.5 10M17.5 10L13.3333 5.83333M17.5 10H7.5" stroke="#1E1E1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

        self.svg_add_templates = svg_add_t
        self.svg_add_atalhos   = svg_add_a

        def icon_btn(svg):
            b = QPushButton()
            b.setIcon(create_svg_icon(svg, 20))
            b.setIconSize(QSize(20, 20))
            b.setFixedSize(40, 40)
            b.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:8px;}QPushButton:hover{background:rgba(0,0,0,0.05);}")
            return b

        self.btn_search = icon_btn(svg_search)
        self.btn_add    = icon_btn(svg_add_t)
        self.btn_add.clicked.connect(self.show_add_overlay)
        self.btn_config = icon_btn(svg_config)
        self.btn_sair   = icon_btn(svg_sair)

        footer.addWidget(self.btn_search)
        footer.addStretch()
        footer.addWidget(self.btn_add)
        footer.addWidget(self.btn_config)
        footer.addWidget(self.btn_sair)
        cl.addLayout(footer)

        container.setLayout(cl)
        ml = QVBoxLayout(); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
        ml.addWidget(container)
        self.setLayout(ml)

        last = self.firebase.get_config('last_tab', MainMenu._last_tab)
        if last == 'atalhos':
            self.show_atalhos_tab()
        else:
            self.show_templates_tab()

    # ── estilos ───────────────────────────────────────────────────────────────
    def update_tab_styles(self, selected='templates'):
        sel_t = selected == 'templates'
        self.btn_templates.setStyleSheet(f"""
            QPushButton {{
                background-color: {'#B97E88' if sel_t else 'transparent'};
                color: {'white' if sel_t else 'black'};
                border: none; padding: 10px 20px; border-radius: 8px;
                font-family: 'Instrument Sans'; font-size: 14px;
            }}""")
        self.btn_atalhos.setStyleSheet(f"""
            QPushButton {{
                background-color: {'#499714' if not sel_t else 'transparent'};
                color: {'white' if not sel_t else 'black'};
                border: none; padding: 10px 20px; border-radius: 8px;
                font-family: 'Instrument Sans'; font-size: 14px;
            }}""")
        self.btn_add.setIcon(create_svg_icon(self.svg_add_templates if sel_t else self.svg_add_atalhos, 20))

    def update_sub_tabs_styles(self, selected='meus', tab_type='templates'):
        color = '#82414C' if tab_type == 'templates' else '#499714'
        sel_m = selected == 'meus'
        def style(active):
            c = color if active else 'black'
            b = f'2px solid {color}' if active else '2px solid transparent'
            return f"""QPushButton {{
                background:transparent; color:{c}; border:none;
                border-bottom:{b}; border-radius:0px;
                padding:5px 10px; font-family:'Inter'; font-size:11px;
            }}"""
        self.btn_meus.setStyleSheet(style(sel_m))
        self.btn_setor.setStyleSheet(style(not sel_m))

    def on_sub_tab_click(self, selected):
        tab = MainMenu._last_tab
        if tab == 'templates':
            MainMenu._last_sub_tab_templates = selected
            self.update_sub_tabs_styles(selected, 'templates')
            self._load_templates(apenas_meus=(selected == 'meus'))
        else:
            MainMenu._last_sub_tab_atalhos = selected
            self.update_sub_tabs_styles(selected, 'atalhos')
            self._load_atalhos(apenas_meus=(selected == 'meus'))

    # ── abas principais ───────────────────────────────────────────────────────
    def show_templates_tab(self):
        MainMenu._last_tab = 'templates'
        self.firebase.set_config('last_tab', 'templates')
        self.update_tab_styles('templates')
        self.btn_meus.setText('Meus templates')
        self.btn_setor.setText('Templates do setor')
        sub = MainMenu._last_sub_tab_templates
        self.update_sub_tabs_styles(sub, 'templates')
        self._load_templates(apenas_meus=(sub == 'meus'))

    def show_atalhos_tab(self):
        MainMenu._last_tab = 'atalhos'
        self.firebase.set_config('last_tab', 'atalhos')
        self.update_tab_styles('atalhos')
        self.btn_meus.setText('Meus atalhos')
        self.btn_setor.setText('Atalhos do setor')
        sub = MainMenu._last_sub_tab_atalhos
        self.update_sub_tabs_styles(sub, 'atalhos')
        self._load_atalhos(apenas_meus=(sub == 'meus'))

    def _clear_content(self):
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # ── lista de templates ────────────────────────────────────────────────────
    def _load_templates(self, apenas_meus=False):
        self._clear_content()
        uid   = self.user_data['uid']
        setor = self.user_data['setor']

        templates = (self.firebase.get_templates_meus(uid) if apenas_meus
                     else self.firebase.get_templates_setor(setor))

        if not templates:
            lbl = QLabel('Nenhum template encontrado')
            lbl.setStyleSheet('color:#999; font-style:italic; padding:20px;')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(lbl)
        else:
            for t in templates:
                self.content_layout.addWidget(self._template_card(t, apenas_meus))

        self.content_layout.addStretch()

    def _template_card(self, t, apenas_meus):
        card = QWidget()
        card.setStyleSheet("""
            QWidget { background-color:white; border-radius:8px; border:1px solid #e0e0e0; }
            QWidget:hover { border:1px solid #B97E88; }
        """)
        lay = QVBoxLayout(); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(4)

        nome_lbl = QLabel(t['nome'])
        nome_lbl.setStyleSheet('font-size:13px; font-weight:bold; color:#2d2d2d;')
        lay.addWidget(nome_lbl)

        preview = t['texto'][:60] + '...' if len(t['texto']) > 60 else t['texto']
        prev_lbl = QLabel(preview)
        prev_lbl.setStyleSheet('font-size:11px; color:#888;')
        lay.addWidget(prev_lbl)

        if t.get('atalho'):
            atl_lbl = QLabel(f"Atalho: {t['atalho']}")
            atl_lbl.setStyleSheet('font-size:10px; color:#B97E88;')
            lay.addWidget(atl_lbl)

        # botões (só aparecem se é do próprio usuário)
        if t.get('usuario_id') == self.user_data['uid']:
            btns = QHBoxLayout(); btns.addStretch()
            b_edit = QPushButton('✎ Editar')
            b_edit.setStyleSheet("QPushButton{color:#406e54;background:transparent;border:none;font-size:11px;}QPushButton:hover{text-decoration:underline;}")
            b_edit.clicked.connect(lambda _, tmpl=t: self.edit_template(tmpl))
            b_del = QPushButton('🗑 Excluir')
            b_del.setStyleSheet("QPushButton{color:#82414c;background:transparent;border:none;font-size:11px;}QPushButton:hover{text-decoration:underline;}")
            b_del.clicked.connect(lambda _, tmpl=t: self.delete_template(tmpl))
            btns.addWidget(b_edit); btns.addWidget(b_del)
            lay.addLayout(btns)

        card.setLayout(lay)
        return card

    # ── lista de atalhos ──────────────────────────────────────────────────────
    def _load_atalhos(self, apenas_meus=False):
        self._clear_content()
        uid   = self.user_data['uid']
        setor = self.user_data['setor']

        shortcuts = (self.firebase.get_shortcuts_meus(uid) if apenas_meus
                     else self.firebase.get_shortcuts_setor(setor))

        if not shortcuts:
            lbl = QLabel('Nenhum atalho encontrado')
            lbl.setStyleSheet('color:#999; font-style:italic; padding:20px;')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(lbl)
        else:
            for s in shortcuts:
                self.content_layout.addWidget(self._shortcut_card(s))

        self.content_layout.addStretch()

    def _shortcut_card(self, s):
        card = QWidget()
        cor_borda = '#499714' if s['ativo'] else '#e0e0e0'
        card.setStyleSheet(f"QWidget {{ background-color:white; border-radius:8px; border:1px solid {cor_borda}; }}")
        lay = QVBoxLayout(); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(4)

        h = QHBoxLayout()
        nome_lbl = QLabel(s['nome'])
        nome_lbl.setStyleSheet('font-size:13px; font-weight:bold; color:#2d2d2d;')
        h.addWidget(nome_lbl)
        h.addStretch()
        status = QLabel('● Ativo' if s['ativo'] else '○ Inativo')
        status.setStyleSheet(f"font-size:10px; color:{'#499714' if s['ativo'] else '#999'};")
        h.addWidget(status)
        lay.addLayout(h)

        info = QLabel(f"{len(s['acoes'])} ação(ões)  |  Atalho: {s.get('tecla_atalho','—')}")
        info.setStyleSheet('font-size:11px; color:#888;')
        lay.addWidget(info)

        if s.get('usuario_id') == self.user_data['uid']:
            btns = QHBoxLayout(); btns.addStretch()
            b_tog = QPushButton('⏸ Desativar' if s['ativo'] else '▶ Ativar')
            b_tog.setStyleSheet("QPushButton{color:#406e54;background:transparent;border:none;font-size:11px;}QPushButton:hover{text-decoration:underline;}")
            b_edit = QPushButton('✎ Editar')
            b_edit.setStyleSheet("QPushButton{color:#406e54;background:transparent;border:none;font-size:11px;}QPushButton:hover{text-decoration:underline;}")
            b_del = QPushButton('🗑 Excluir')
            b_del.setStyleSheet("QPushButton{color:#82414c;background:transparent;border:none;font-size:11px;}QPushButton:hover{text-decoration:underline;}")
            btns.addWidget(b_tog); btns.addWidget(b_edit); btns.addWidget(b_del)
            lay.addLayout(btns)

        card.setLayout(lay)
        return card
    def show_add_overlay(self):
        self.overlay_widget = OverlayDialog(self)

        title = QLabel("Criação de template")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:16px; font-weight:bold; color:black; padding:0px 0px 15px 0px;")
        self.overlay_widget.add_content(title)

        def styled_input(ph):
            w = QLineEdit(); w.setPlaceholderText(ph)
            w.setStyleSheet("""
                QLineEdit { padding:10px; border:2px solid #ddd; border-radius:8px; font-size:13px; background:white; color:black; }
                QLineEdit:focus { border:2px solid #B97E88; }
                QLineEdit::placeholder { color:#999; }
            """)
            return w

        self.tpl_titulo  = styled_input("Título do template")
        self.tpl_atalho  = styled_input("Atalho (opcional, ex: otb)")
        self.overlay_widget.add_content(self.tpl_titulo)
        self.overlay_widget.add_content(self.tpl_atalho)

        frame = QFrame()
        frame.setStyleSheet("QFrame { border:2px solid #ddd; border-radius:8px; background:white; } QFrame:focus-within { border:2px solid #B97E88; }")
        fl = QVBoxLayout(); fl.setContentsMargins(0,0,0,0)
        self.tpl_conteudo = QTextEdit(); self.tpl_conteudo.setPlaceholderText("Conteúdo do template")
        self.tpl_conteudo.setStyleSheet("QTextEdit { padding:8px; border:none; font-size:13px; background:transparent; color:black; }")
        palette = self.tpl_conteudo.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, QColor("#999"))
        self.tpl_conteudo.setPalette(palette)
        fl.addWidget(self.tpl_conteudo); frame.setLayout(fl)
        self.overlay_widget.add_content(frame)
        self._overlay_frame = frame

        btns = QHBoxLayout(); btns.setSpacing(10); btns.addStretch()
        b_criar = QPushButton("Criar"); b_criar.setFixedWidth(90)
        b_criar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:white;background:#82414C;border:none;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:#6d3640;}")
        b_criar.clicked.connect(self.create_template)
        b_cancel = QPushButton("Cancelar"); b_cancel.setFixedWidth(90)
        b_cancel.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:#82414C;background:transparent;border:2px solid #82414C;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:rgba(130,65,76,0.1);}")
        b_cancel.clicked.connect(self.overlay_widget.close)
        btns.addWidget(b_criar); btns.addWidget(b_cancel)
        bw = QWidget(); bw.setLayout(btns)
        self.overlay_widget.add_content(bw)
        self.overlay_widget.show()

    def create_template(self):
        titulo   = self.tpl_titulo.text().strip()
        atalho   = self.tpl_atalho.text().strip()
        conteudo = self.tpl_conteudo.toPlainText().strip()

        if not titulo:
            self.show_field_error(self.tpl_titulo, "Este campo é obrigatório")
            return
        if not conteudo:
            self.show_field_error(self._overlay_frame, "Este campo é obrigatório")
            self.tpl_conteudo.setFocus()
            return

        ok = self.firebase.add_template(
            titulo, conteudo, atalho,
            self.user_data['uid'], self.user_data['setor']
        )
        if ok:
            self.overlay_widget.close()
            NotificationWidget('✓ Template criado!').show()
            self.show_templates_tab()
        else:
            QMessageBox.critical(self, "Erro", "Falha ao salvar no Firebase. Verifique sua conexão.")

    def edit_template(self, t):
        if self.add_window and self.add_window.isVisible():
            self.add_window.close()
        self.add_window = EditTemplateWindow(self.firebase, self.user_data, menu_ref=self,
                                              doc_id=t['id'], nome=t['nome'],
                                              texto=t['texto'], atalho=t.get('atalho',''))
        self.add_window.show(); self.add_window.raise_(); self.add_window.activateWindow()

    def delete_template(self, t):
        msg = QMessageBox(); msg.setWindowTitle('Confirmar'); msg.setText('Excluir este template?')
        b_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        msg.exec()
        if msg.clickedButton() == b_sim:
            self.firebase.delete_template(t['id'])
            NotificationWidget('✓ Template excluído!').show()
            self.show_templates_tab()

    def show_field_error(self, widget, message):
        original_style = widget.styleSheet()
        if isinstance(widget, QLineEdit):
            widget.setStyleSheet("QLineEdit{padding:10px;border:2px solid #ff4444;border-radius:8px;font-size:13px;background:white;color:black;}")
        elif isinstance(widget, QFrame):
            widget.setStyleSheet("QFrame{border:2px solid #ff4444;border-radius:8px;background:white;}")
        widget.setToolTip(message)
        original_pos = widget.pos()
        shake = QSequentialAnimationGroup()
        for _ in range(3):
            a1 = QPropertyAnimation(widget, b"pos"); a1.setDuration(50)
            a1.setStartValue(original_pos); a1.setEndValue(QPoint(original_pos.x()+10, original_pos.y()))
            a2 = QPropertyAnimation(widget, b"pos"); a2.setDuration(50)
            a2.setStartValue(QPoint(original_pos.x()+10, original_pos.y())); a2.setEndValue(QPoint(original_pos.x()-10, original_pos.y()))
            shake.addAnimation(a1); shake.addAnimation(a2)
        ab = QPropertyAnimation(widget, b"pos"); ab.setDuration(50)
        ab.setStartValue(QPoint(original_pos.x()-10, original_pos.y())); ab.setEndValue(original_pos)
        shake.addAnimation(ab); shake.start()
        QTimer.singleShot(3000, lambda: (widget.setStyleSheet(original_style), widget.setToolTip("")))
        if isinstance(widget, QLineEdit): widget.setFocus()


# ---------------------------------------------------------------------------
# EditTemplateWindow
# ---------------------------------------------------------------------------
class EditTemplateWindow(QWidget):
    def __init__(self, firebase, user_data, menu_ref=None, doc_id=None, nome='', texto='', atalho=''):
        super().__init__(None)
        self.firebase      = firebase
        self.user_data     = user_data
        self.menu_reference = menu_ref
        self.doc_id        = doc_id
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.init_ui(nome, texto, atalho)

    def closeEvent(self, event):
        self.menu_reference = None; event.accept()

    def init_ui(self, nome, texto, atalho):
        self.setWindowTitle('Editar Template')
        self.setFixedSize(500, 400)
        lay = QVBoxLayout()

        lay.addWidget(QLabel('Nome do template:'))
        self.nome_input = QLineEdit(nome)
        lay.addWidget(self.nome_input)

        lay.addWidget(QLabel('Texto do template:'))
        self.texto_input = QTextEdit(); self.texto_input.setPlainText(texto)
        lay.addWidget(self.texto_input)

        lay.addWidget(QLabel('Atalho de texto (opcional):'))
        self.atalho_input = QLineEdit(atalho or '')
        lay.addWidget(self.atalho_input)

        bl = QHBoxLayout()
        b_salvar = QPushButton('✓ Salvar')
        b_salvar.setStyleSheet("QPushButton{background:#4CAF50;color:white;padding:10px;font-weight:bold;border:none;border-radius:3px;}QPushButton:hover{background:#45a049;}")
        b_salvar.clicked.connect(self.salvar)
        bl.addWidget(b_salvar)
        bl.addWidget(QPushButton('✗ Cancelar', clicked=self.close))
        lay.addLayout(bl)
        self.setLayout(lay)

    def salvar(self):
        nome   = self.nome_input.text().strip()
        texto  = self.texto_input.toPlainText().strip()
        atalho = self.atalho_input.text().strip() or ''
        if not nome or not texto:
            QMessageBox.warning(self, 'Erro', 'Preencha nome e texto!'); return
        self.firebase.update_template(self.doc_id, nome, texto, atalho)
        NotificationWidget('✓ Template atualizado!').show()
        if self.menu_reference:
            QTimer.singleShot(100, self.menu_reference.show_templates_tab)
        self.close()



# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    firebase = FirebaseAuth()

    global circle
    circle = None

    login_window = LoginWindow(firebase)

    def on_login_success(user_data):
        global circle
        circle = FloatingCircle(firebase, user_data)
        circle.show(); circle.raise_(); circle.activateWindow()

        listener = KeyboardListener(firebase, user_data)
        listener.start()

    login_window.login_success.connect(on_login_success)
    login_window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()