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
                              QTabWidget, QFrame, QStackedWidget)
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
        self._cache_nomes     = {}  # chave: uid → nome

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

    def get_user_nome(self, uid):
        if uid not in self._cache_nomes:
            data = self.get_user_data(uid)
            self._cache_nomes[uid] = data.get('nome', '?') if data else '?'
        return self._cache_nomes[uid]

    def get_pending_users(self):
        resp = requests.get(self._base('pending_users'), headers=self._headers())
        if resp.status_code != 200:
            return []
        users = []
        for doc in resp.json().get('documents', []):
            doc_uid = doc['name'].split('/')[-1]
            u = self._fields_to_dict(doc.get('fields', {}))
            u['uid'] = doc_uid  # sempre usa o ID do documento, nunca campo interno
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
        requests.patch(self._base('usuarios', uid), headers=self._headers(), json={"fields": {
            "nome": {"stringValue": nome}, "setor": {"stringValue": setor},
            "email": {"stringValue": email}, "aprovado": {"booleanValue": True}, "is_admin": {"booleanValue": False},
        }})
        requests.delete(self._base('pending_users', uid), headers=self._headers())
        return True

    def reject_user(self, uid):
        requests.delete(self._base('pending_users', uid), headers=self._headers())
        return True

    def delete_user(self, uid):
        requests.delete(self._base('usuarios', uid), headers=self._headers())
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

    def add_atalho(self, titulo, comando_tipo, comando_valor, acoes, usuario_id, setor):
        data = {"fields": {
            "titulo":         {"stringValue": titulo},
            "comando_tipo":   {"stringValue": comando_tipo},
            "comando_valor":  {"stringValue": comando_valor or ""},
            "acoes":          {"stringValue": json.dumps(acoes)},
            "usuario_id":     {"stringValue": usuario_id},
            "setor":          {"stringValue": setor},
        }}
        resp = requests.post(self._base('atalhos'), headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._invalidate_cache()
        return resp.status_code == 200

    def delete_atalho(self, doc_id):
        resp = requests.delete(self._base('atalhos', doc_id), headers=self._headers())
        if resp.status_code == 200:
            self._cache_shortcuts.clear()
        return resp.status_code == 200

    def update_atalho(self, doc_id, titulo, comando_tipo, comando_valor, acoes):
        data = {"fields": {
            "titulo":        {"stringValue": titulo},
            "comando_tipo":  {"stringValue": comando_tipo},
            "comando_valor": {"stringValue": comando_valor},
            "acoes":         {"stringValue": json.dumps(acoes)},
        }}
        mask = "updateMask.fieldPaths=titulo&updateMask.fieldPaths=comando_tipo&updateMask.fieldPaths=comando_valor&updateMask.fieldPaths=acoes"
        resp = requests.patch(self._base('atalhos', doc_id) + '?' + mask, headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._cache_shortcuts.clear()
        return resp.status_code == 200

    def update_atalho_ativo(self, doc_id, ativo):
        data = {"fields": {"ativo": {"booleanValue": ativo}}}
        url = self._base('atalhos', doc_id) + '?updateMask.fieldPaths=ativo'
        requests.patch(url, headers=self._headers(), json=data)
        self._cache_shortcuts.clear()

    def update_atalho_descricao(self, doc_id, descricao):
        data = {"fields": {"descricao": {"stringValue": descricao}}}
        url = self._base('atalhos', doc_id) + '?updateMask.fieldPaths=descricao'
        resp = requests.patch(url, headers=self._headers(), json=data)
        if resp.status_code == 200:
            self._cache_shortcuts.clear()

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

    def get_atalhos_meus(self, usuario_id):
        key = ('atalhos_uid', usuario_id)
        if key not in self._cache_shortcuts:
            self._cache_shortcuts[key] = self._query_atalhos('usuario_id', usuario_id)
        return self._cache_shortcuts[key]

    def get_atalhos_setor(self, setor):
        key = ('atalhos_setor', setor)
        if key not in self._cache_shortcuts:
            self._cache_shortcuts[key] = self._query_atalhos('setor', setor)
        return self._cache_shortcuts[key]

    def _query_atalhos(self, field, value):
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents:runQuery"
        body = {"structuredQuery": {"from": [{"collectionId": "atalhos"}], "where": {"fieldFilter": {"field": {"fieldPath": field}, "op": "EQUAL", "value": {"stringValue": value}}}}}
        resp = requests.post(url, headers=self._headers(), json=body)
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json():
            if 'document' not in item:
                continue
            doc = item['document']
            f = doc.get('fields', {})
            results.append({
                'id':            doc['name'].split('/')[-1],
                'titulo':        f.get('titulo',        {}).get('stringValue', ''),
                'descricao':     f.get('descricao',     {}).get('stringValue', ''),
                'comando_tipo':  f.get('comando_tipo',  {}).get('stringValue', ''),
                'comando_valor': f.get('comando_valor', {}).get('stringValue', ''),
                'acoes':         json.loads(f.get('acoes', {}).get('stringValue', '[]')),
                'ativo':         f.get('ativo', {}).get('booleanValue', True),
                'usuario_id':    f.get('usuario_id',    {}).get('stringValue', ''),
                'setor':         f.get('setor',         {}).get('stringValue', ''),
            })
        return results

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
        setor = self.user_data['setor']

        # Verificar templates
        for t in self.firebase.get_templates_setor(setor):
            if t['atalho'] and t['atalho'].lower() == self.typed_text.strip().lower():
                self._apagar_e_digitar(t['texto'], len(self.typed_text) + 1)
                return

        # Verificar novos atalhos (shortcut)
        for s in self.firebase.get_atalhos_setor(setor):
            if not s.get('ativo', True): continue
            if s.get('comando_tipo') == 'shortcut':
                if s.get('comando_valor', '').lower() == self.typed_text.strip().lower():
                    n = len(self.typed_text) + 1
                    def run(acoes=s['acoes'], nb=n):
                        for _ in range(nb):
                            self.keyboard_controller.press(Key.backspace)
                            self.keyboard_controller.release(Key.backspace)
                            time.sleep(0.01)
                        time.sleep(0.05)
                        self.execute_atalho(acoes)
                    threading.Thread(target=run, daemon=True).start()
                    return

        # Verificar shortcuts antigos
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

        # Verificar novos atalhos (alt_tecla)
        for s in self.firebase.get_atalhos_setor(setor):
            if not s.get('ativo', True): continue
            if s.get('comando_tipo') == 'alt_tecla':
                teclas = [t.strip() for t in s.get('comando_valor', '').split(',')]
                if any(t.upper() == char.upper() for t in teclas):
                    def run(acoes=s['acoes']):
                        time.sleep(0.1)
                        self.execute_atalho(acoes)
                    threading.Thread(target=run, daemon=True).start()
                    return

        # Verificar shortcuts antigos
        for s in self.firebase.get_shortcuts_setor(setor):
            if not s['ativo']: continue
            tecla = s.get('tecla_atalho', '')
            if len(tecla) <= 2 and tecla.upper() == char.upper():
                def run(acoes=s['acoes']):
                    time.sleep(0.1)
                    self.execute_shortcut(acoes)
                threading.Thread(target=run, daemon=True).start()
                return

    def execute_atalho(self, acoes):
        """Executa lista de ações no formato estruturado (dicts com tipo, x, y, etc)."""
        def run():
            try:
                time.sleep(0.1)
                for acao in acoes:
                    # suporte a formato antigo (string) e novo (dict)
                    if isinstance(acao, str):
                        import re
                        m = re.match(r'Clicar com o bot[aã]o E (\d+) vez', acao)
                        if m:
                            for _ in range(int(m.group(1))): self.mouse_controller.click(Button.left, 1); time.sleep(0.08)
                        elif re.search(r'bot[aã]o D\.', acao): self.mouse_controller.click(Button.right, 1)
                        elif re.search(r'bot[aã]o do meio\.', acao): self.mouse_controller.click(Button.middle, 1)
                        elif re.match(r'Esperar (\d+) ms', acao): time.sleep(int(re.match(r'Esperar (\d+) ms', acao).group(1)) / 1000.0)
                        time.sleep(0.05)
                        continue
                    tipo = acao.get('tipo', '')
                    if tipo == 'click':
                        x, y = acao.get('x'), acao.get('y')
                        if x is not None and y is not None:
                            self.mouse_controller.position = (x, y)
                            time.sleep(0.05)
                        botao = acao.get('botao', 'E')
                        qtd   = acao.get('qtd', 1)
                        btn   = Button.left if botao == 'E' else (Button.right if botao == 'D' else Button.middle)
                        for _ in range(qtd):
                            self.mouse_controller.click(btn, 1)
                            if qtd > 1: time.sleep(0.08)
                    elif tipo == 'esperar':
                        time.sleep(acao.get('ms', 0) / 1000.0)
                    time.sleep(0.05)
            except Exception as e:
                print(f"Erro ao executar atalho: {e}")
        threading.Thread(target=run, daemon=True).start()

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
class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)
    def __init__(self, checked=True, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(36, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    def isChecked(self): return self._checked
    def setChecked(self, v): self._checked = v; self.update()
    def mousePressEvent(self, e):
        self._checked = not self._checked
        self.update()
        self.toggled.emit(self._checked)
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor("#88C22B") if self._checked else QColor("#C0C0C0")
        p.setBrush(bg); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 36, 20, 10, 10)
        p.setBrush(QColor("white")); p.setPen(Qt.PenStyle.NoPen)
        cx = 18 if self._checked else 2
        p.drawEllipse(cx, 2, 16, 16)


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
        self.content_card.setStyleSheet("QWidget { background-color:#DEDDD2; border-radius:15px; }")

        self.card_layout = QVBoxLayout()
        self.card_layout.setContentsMargins(20, 10, 20, 20)
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
            if not getattr(self, '_block_outside_close', False):
                self.close()
        else:
            event.ignore()


def show_confirm(parent, message, on_confirm):
    """Mostra um diálogo de confirmação com fundo borrado sobre o parent."""
    overlay = OverlayDialog(parent)
    overlay.content_card.setFixedSize(260, 120)

    lbl = QLabel(message)
    lbl.setStyleSheet("font-family:'Instrument Sans'; font-size:13px; color:#2d2d2d; background:transparent; border:none;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    overlay.add_content(lbl)

    overlay.card_layout.addStretch()

    btns = QHBoxLayout()
    btns.setSpacing(10)

    b_nao = QPushButton("Não")
    b_nao.setFixedHeight(36)
    b_nao.setStyleSheet("""
        QPushButton { font-family:'Instrument Sans'; font-size:13px; color:#2d2d2d;
            background:transparent; border:2px solid #ccc; border-radius:8px; }
        QPushButton:hover { background:rgba(0,0,0,0.05); }
    """)
    b_nao.clicked.connect(overlay.close)

    b_sim = QPushButton("Sim")
    b_sim.setFixedHeight(36)
    b_sim.setStyleSheet("""
        QPushButton { font-family:'Instrument Sans'; font-size:13px; color:white;
            background:#900B09; border:none; border-radius:8px; }
        QPushButton:hover { background:#7a0908; }
    """)
    def _confirm():
        overlay.close()
        on_confirm()
    b_sim.clicked.connect(_confirm)

    btns.addWidget(b_nao)
    btns.addWidget(b_sim)
    bw = QWidget()
    bw.setStyleSheet("background:transparent; border:none;")
    bw.setLayout(btns)
    overlay.add_content(bw)

    overlay.show()
    return overlay


# ---------------------------------------------------------------------------
# MainMenu
# ---------------------------------------------------------------------------
class _ClickOutsideOverlay(QWidget):
    """Overlay invisível que detecta cliques fora do menu alvo."""
    def __init__(self, geometry, target):
        super().__init__(None)
        self._target = target
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        pass  # não desenha nada — completamente transparente

    def mousePressEvent(self, event):
        gpos = event.globalPosition().toPoint()
        if self._target.geometry().contains(gpos):
            event.ignore()
            return
        self._target.close()


class MainMenu(QWidget):
    _last_tab             = 'templates'
    _last_sub_tab_templates = 'meus'
    _last_sub_tab_atalhos   = 'meus'
    _templates_loaded  = pyqtSignal(list, bool)
    _usuarios_loaded   = pyqtSignal(list)

    def __init__(self, firebase, user_data, parent=None):
        super().__init__(parent)
        self.firebase    = firebase
        self.user_data   = user_data
        self.circle_parent = parent
        self.add_window  = None
        self._templates_loaded.connect(self._on_templates_loaded)
        self._usuarios_loaded.connect(self._on_usuarios_loaded)
        self.init_ui()
        self.init_ui_content()

    # ── fechamento ────────────────────────────────────────────────────────────
    def _reset_circle(self):
        if self.circle_parent:
            self.circle_parent.menu_open = False
            self.circle_parent._animate_circle(0.85, 0.6)

    # ── UI ───────────────────────────────────────────────────────────────────
    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(450, 520)

    def showEvent(self, event):
        super().showEvent(event)
        self._pynput_listener = None
        def _start_listener():
            import time; time.sleep(0.3)  # ignorar o clique que abriu
            from pynput import mouse as _mouse
            def _on_click(x, y, button, pressed):
                if not pressed or button != _mouse.Button.left:
                    return
                if not self.isVisible():
                    return False
                from PyQt6.QtCore import QRect
                geo = self.geometry()
                if not geo.contains(int(x), int(y)):
                    # não fechar se overlay de criação/edição estiver aberto
                    overlay = getattr(self, 'overlay_widget', None)
                    if overlay and overlay.isVisible() and getattr(overlay, '_block_outside_close', False):
                        return
                    QTimer.singleShot(0, self.close)
                    return False
            self._pynput_listener = _mouse.Listener(on_click=_on_click)
            self._pynput_listener.start()
        import threading
        threading.Thread(target=_start_listener, daemon=True).start()

    def closeEvent(self, event):
        if hasattr(self, '_pynput_listener') and self._pynput_listener:
            self._pynput_listener.stop()
            self._pynput_listener = None
        if hasattr(self, '_bg_overlay') and self._bg_overlay:
            self._bg_overlay.close()
            self._bg_overlay = None
        self._reset_circle()
        event.accept()

    def hideEvent(self, event):
        self._reset_circle()
        event.accept()

    # ── init_ui ───────────────────────────────────────────────────────────────
    def init_ui_content(self):
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
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea { border:none; background:transparent; }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #c0c0c0;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #a0a0a0;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        self.content_area = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 8, 0)
        self.content_layout.setSpacing(6)
        self.content_area.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_area)
        cl.addWidget(self.scroll_area, stretch=1)

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

        # campo de pesquisa com lupa interna
        self.search_container = QWidget()
        self.search_container.setVisible(False)
        self.search_container.setStyleSheet("""
            QWidget {
                background: #929187;
                border-radius: 12px;
            }
        """)
        sc_layout = QHBoxLayout(self.search_container)
        sc_layout.setContentsMargins(12, 0, 8, 0)
        sc_layout.setSpacing(4)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Pesquisar...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                font-family: 'Instrument Sans';
                font-size: 13px;
                color: white;
            }
            QLineEdit::placeholder { color: rgba(255,255,255,0.6); }
        """)
        self.search_input.setFixedHeight(36)
        self.search_input.textChanged.connect(self.on_search_changed)

        svg_search_inner = """<svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M17.5 17.5L13.875 13.875M15.8333 9.16667C15.8333 12.8486 12.8486 15.8333 9.16667 15.8333C5.48477 15.8333 2.5 12.8486 2.5 9.16667C2.5 5.48477 5.48477 2.5 9.16667 2.5C12.8486 2.5 15.8333 5.48477 15.8333 9.16667Z" stroke="#1E1E1E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
        self.lupa_btn = QPushButton()
        self.lupa_btn.setIcon(create_svg_icon(svg_search_inner, 18))
        self.lupa_btn.setIconSize(QSize(18, 18))
        self.lupa_btn.setFixedSize(28, 28)
        self.lupa_btn.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self.lupa_btn.clicked.connect(self.toggle_search)
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        self._lupa_effect = QGraphicsOpacityEffect()
        self._lupa_effect.setOpacity(1.0)
        self.lupa_btn.setGraphicsEffect(self._lupa_effect)

        sc_layout.addWidget(self.search_input)

        # lupa interna (visível só quando campo aberto, com 60% opacidade)
        svg_search_inner = """<svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M17.5 17.5L13.875 13.875M15.8333 9.16667C15.8333 12.8486 12.8486 15.8333 9.16667 15.8333C5.48477 15.8333 2.5 12.8486 2.5 9.16667C2.5 5.48477 5.48477 2.5 9.16667 2.5C12.8486 2.5 15.8333 5.48477 15.8333 9.16667Z" stroke="#1E1E1E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
        self.lupa_interna = QPushButton()
        self.lupa_interna.setIcon(create_svg_icon(svg_search_inner, 18))
        self.lupa_interna.setIconSize(QSize(18, 18))
        self.lupa_interna.setFixedSize(28, 28)
        self.lupa_interna.setStyleSheet("QPushButton{background:transparent;border:none;}")
        self.lupa_interna.clicked.connect(self.toggle_search)
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        self._lupa_effect = QGraphicsOpacityEffect()
        self._lupa_effect.setOpacity(0.6)
        self.lupa_interna.setGraphicsEffect(self._lupa_effect)
        sc_layout.addWidget(self.lupa_interna)

        self.svg_add_templates = svg_add_t
        self.svg_add_atalhos   = svg_add_a
        self._search_open = False

        def icon_btn(svg):
            b = QPushButton()
            b.setIcon(create_svg_icon(svg, 20))
            b.setIconSize(QSize(20, 20))
            b.setFixedSize(40, 40)
            b.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:8px;}QPushButton:hover{background:rgba(0,0,0,0.05);}")
            return b

        # lupa externa (visível só quando campo fechado)
        self.btn_search = icon_btn(svg_search)
        self.btn_search.clicked.connect(self.toggle_search)
        self.btn_add    = icon_btn(svg_add_t)
        self.btn_add.clicked.connect(self.show_add_overlay)
        self.btn_config = icon_btn(svg_config)
        self.btn_config.clicked.connect(self.show_config_tab)
        self.btn_sair   = icon_btn(svg_sair)
        self.btn_sair.clicked.connect(self.confirmar_sair)

        footer.addWidget(self.search_container, stretch=1)
        footer.addWidget(self.btn_search)
        footer.addStretch()
        footer.addWidget(self.btn_add)
        footer.addWidget(self.btn_config)
        footer.addWidget(self.btn_sair)
        cl.addLayout(footer)

        container.setLayout(cl)

        # tela de configurações (página 1 do stack) — conteúdo gerado em show_config_tab
        self.config_page = QWidget()
        self.config_page.setStyleSheet("QWidget { background-color:#DEDDD2; border-radius:10px; }")
        config_layout = QVBoxLayout(self.config_page)
        config_layout.setContentsMargins(15, 15, 15, 15)
        config_layout.setSpacing(10)
        self.config_content_layout = config_layout

        self.config_scroll = QScrollArea()
        self.config_scroll.setWidget(self.config_page)
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.config_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.config_scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.config_scroll.viewport().setStyleSheet("background:#DEDDD2; border-radius:10px;")
        self.config_scroll.setStyleSheet("""
            QScrollArea { border:none; background:#DEDDD2; border-radius:10px; }
            QScrollBar:vertical { background:transparent; width:6px; margin:0; border-radius:3px; }
            QScrollBar::handle:vertical { background:#c0c0c0; border-radius:3px; min-height:20px; }
            QScrollBar::handle:vertical:hover { background:#a0a0a0; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        """)

        self.stack = QStackedWidget()
        self.stack.addWidget(container)        # índice 0 — menu principal
        self.stack.addWidget(self.config_scroll) # índice 1 — configurações

        ml = QVBoxLayout(); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
        ml.addWidget(self.stack)
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
        self._current_tab = 'templates'
        self.firebase.set_config('last_tab', 'templates')
        self.update_tab_styles('templates')
        self.btn_meus.setText('Meus templates')
        self.btn_setor.setText('Templates do setor')
        sub = MainMenu._last_sub_tab_templates
        self.update_sub_tabs_styles(sub, 'templates')
        self._load_templates(apenas_meus=(sub == 'meus'))

    def show_atalhos_tab(self):
        MainMenu._last_tab = 'atalhos'
        self._current_tab = 'atalhos'
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

        import threading
        def run():
            result = (self.firebase.get_templates_meus(uid) if apenas_meus
                      else self.firebase.get_templates_setor(setor))
            # para aba do setor, pre-carrega nomes dos criadores (usa cache)
            if not apenas_meus:
                for t in result:
                    self.firebase.get_user_nome(t['usuario_id'])
            self._templates_loaded.emit(result, apenas_meus)

        threading.Thread(target=run, daemon=True).start()

    def _on_templates_loaded(self, templates, apenas_meus):
        self._clear_content()
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
        has_atalho = bool(t.get('atalho'))
        card.setFixedHeight(80 if has_atalho else 65)
        card.setStyleSheet("QWidget { border: 1.5px solid #909090; border-radius: 10px; background: transparent; }")

        # layout principal: conteúdo à esquerda, botões à direita
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 8, 8, 8)
        outer.setSpacing(0)

        # coluna de texto
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        nome = QLabel(t['nome'])
        nome.setStyleSheet("font-family: 'Instrument Sans'; font-size: 13px; font-weight: 500; color: black; background: transparent; border: none;")
        text_col.addWidget(nome)

        preview = QLabel()
        preview.setStyleSheet("font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: #828282; background: transparent; border: none;")
        fm = preview.fontMetrics()
        elided = fm.elidedText(t['texto'].replace('\n', ' '), Qt.TextElideMode.ElideRight, 330)
        preview.setText(elided)
        text_col.addWidget(preview)

        if t.get('atalho'):
            atalho = QLabel(f"atalho: <b>{t['atalho']}</b>")
            atalho.setStyleSheet("font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: #88C22B; background: transparent; border: none;")
            atalho.setTextFormat(Qt.TextFormat.RichText)
            text_col.addWidget(atalho)

        outer.addLayout(text_col, stretch=1)

        # coluna de botões (dono na aba "meus") ou "Criado por" (aba do setor)
        if t.get('usuario_id') == self.user_data['uid'] and apenas_meus:
            svg_del = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2 3.99998H3.33333M3.33333 3.99998H14M3.33333 3.99998L3.33333 13.3333C3.33333 13.6869 3.47381 14.0261 3.72386 14.2761C3.97391 14.5262 4.31304 14.6666 4.66667 14.6666H11.3333C11.687 14.6666 12.0261 14.5262 12.2761 14.2761C12.5262 14.0261 12.6667 13.6869 12.6667 13.3333V3.99998M5.33333 3.99998V2.66665C5.33333 2.31302 5.47381 1.97389 5.72386 1.72384C5.97391 1.47379 6.31304 1.33331 6.66667 1.33331H9.33333C9.68696 1.33331 10.0261 1.47379 10.2761 1.72384C10.5262 1.97389 10.6667 2.31302 10.6667 2.66665V3.99998" stroke="#900B09" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
            svg_edit = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M3.33333 12.6667H4.28333L10.8 6.15L9.85 5.2L3.33333 11.7167V12.6667ZM2 14V11.1667L10.8 2.38333C10.9333 2.26111 11.0806 2.16667 11.2417 2.1C11.4028 2.03333 11.5722 2 11.75 2C11.9278 2 12.1 2.03333 12.2667 2.1C12.4333 2.16667 12.5778 2.26667 12.7 2.4L13.6167 3.33333C13.75 3.45556 13.8472 3.6 13.9083 3.76667C13.9694 3.93333 14 4.1 14 4.26667C14 4.44444 13.9694 4.61389 13.9083 4.775C13.8472 4.93611 13.75 5.08333 13.6167 5.21667L4.83333 14H2ZM10.3167 5.68333L9.85 5.2L10.8 6.15L10.3167 5.68333Z" fill="#1D1B20"/></svg>"""

            btn_del = QPushButton()
            btn_del.setIcon(create_svg_icon(svg_del, 16))
            btn_del.setIconSize(QSize(16, 16))
            btn_del.setFixedSize(24, 24)
            btn_del.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;}QPushButton:hover{background:rgba(144,11,9,0.08);}")
            btn_del.clicked.connect(lambda _, tmpl=t: self.delete_template(tmpl))

            btn_edit = QPushButton()
            btn_edit.setIcon(create_svg_icon(svg_edit, 16))
            btn_edit.setIconSize(QSize(16, 16))
            btn_edit.setFixedSize(24, 24)
            btn_edit.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;}QPushButton:hover{background:rgba(0,0,0,0.06);}")
            btn_edit.clicked.connect(lambda _, tmpl=t: self.show_edit_overlay(tmpl))

            btn_col = QVBoxLayout()
            btn_col.setContentsMargins(0, 0, 0, 0)
            btn_col.setSpacing(10)
            btn_col.addWidget(btn_del)
            btn_col.addWidget(btn_edit)
            outer.addLayout(btn_col)

        elif not apenas_meus:
            nome_criador = self.firebase.get_user_nome(t['usuario_id'])
            criado_por = QLabel(f"Criado por: {nome_criador}")
            criado_por.setStyleSheet("font-family:'Instrument Sans'; font-size:10px; font-weight:400; color:black; background:transparent; border:none;")
            criado_por.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
            outer.addWidget(criado_por, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        return card

    # ── lista de atalhos ──────────────────────────────────────────────────────
    def _load_atalhos(self, apenas_meus=False):
        self._clear_content()
        uid   = self.user_data['uid']
        setor = self.user_data['setor']

        atalhos = (self.firebase.get_atalhos_meus(uid) if apenas_meus
                     else self.firebase.get_atalhos_setor(setor))

        if not atalhos:
            lbl = QLabel('Nenhum atalho encontrado')
            lbl.setStyleSheet('color:#999; font-style:italic; padding:20px;')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(lbl)
        else:
            for s in atalhos:
                self.content_layout.addWidget(self._shortcut_card(s, apenas_meus))

        self.content_layout.addStretch()

    def _shortcut_card(self, s, apenas_meus=True):
        descricao_atual = [s.get('descricao', '')]
        eh_dono = s.get('usuario_id') == self.user_data['uid'] and apenas_meus
        card = QWidget()
        card.setFixedHeight(75)
        card.setStyleSheet("QWidget { border: 1.5px solid #909090; border-radius: 10px; background: transparent; }")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(12, 8, 8, 8)
        outer.setSpacing(0)

        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        text_col.setContentsMargins(0, 0, 0, 0)

        titulo = QLabel(s.get('titulo', ''))
        titulo.setStyleSheet("font-family: 'Instrument Sans'; font-size: 13px; font-weight: 500; color: black; background: transparent; border: none;")
        text_col.addWidget(titulo)

        _TXT_VAZIO = "Adicionar descrição do atalho (opcional)"

        if eh_dono:
            desc_lbl = QLabel(descricao_atual[0] if descricao_atual[0] else _TXT_VAZIO)
            desc_lbl.setStyleSheet(f"font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: {'#828282' if descricao_atual[0] else '#b0b0b0'}; background: transparent; border: none;")
            desc_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            _tt = QLabel("Dê dois cliques para editar a descrição")
            _tt.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            _tt.setStyleSheet("QLabel{background:#1D1B20;color:white;font-family:'Inter';font-size:11px;padding:4px 8px;border-radius:4px;}")
            _tt.adjustSize()
            _tt_timer = QTimer(); _tt_timer.setSingleShot(True)
            def _show_tt(_t=_tt):
                pos = QCursor.pos(); _t.move(pos.x()+10, pos.y()+14); _t.show()
            def _hide_tt(_t=_tt, _tm=_tt_timer):
                _tm.stop(); _t.hide()
            _tt_timer.timeout.connect(_show_tt)
            def _enter(e, _tm=_tt_timer): 
                if not _tm.isActive(): _tm.start(200)
            def _leave(e, _ht=None): _hide_tt()
            desc_lbl.enterEvent = _enter
            desc_lbl.leaveEvent = _leave
            desc_edit = QLineEdit(descricao_atual[0])
            desc_edit.setPlaceholderText(_TXT_VAZIO)
            desc_edit.setStyleSheet("QLineEdit{font-family:'Instrument Sans';font-size:11px;color:#828282;background:transparent;border:none;border-bottom:1px solid #C2C0B6;padding:0;}")
            desc_edit.setVisible(False)
            def _entrar_edicao(e=None, _lbl=desc_lbl, _ed=desc_edit, _da=descricao_atual, _ht=_hide_tt):
                _ht()
                _lbl.setVisible(False); _ed.setText(_da[0]); _ed.setVisible(True); _ed.setFocus(); _ed.selectAll()
            def _sair_edicao(_lbl=desc_lbl, _ed=desc_edit, _da=descricao_atual, _sid=s.get('id')):
                novo = _ed.text().strip(); _da[0] = novo
                _lbl.setText(novo if novo else _TXT_VAZIO)
                _lbl.setStyleSheet(f"font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: {'#828282' if novo else '#b0b0b0'}; background: transparent; border: none;")
                _ed.setVisible(False); _lbl.setVisible(True)
                if _sid:
                    import threading as _t
                    _t.Thread(target=lambda: self.firebase.update_atalho_descricao(_sid, novo), daemon=True).start()
            desc_lbl.mouseDoubleClickEvent = lambda e: _entrar_edicao()
            desc_edit.editingFinished.connect(_sair_edicao)
            def _key_desc(e, _ed=desc_edit, _se=_sair_edicao):
                if e.key() == Qt.Key.Key_Escape: _se()
                else: QLineEdit.keyPressEvent(_ed, e)
            desc_edit.keyPressEvent = _key_desc
            text_col.addWidget(desc_lbl)
            text_col.addWidget(desc_edit)
        elif descricao_atual[0]:
            desc_lbl2 = QLabel(descricao_atual[0])
            desc_lbl2.setStyleSheet("font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: #828282; background: transparent; border: none;")
            text_col.addWidget(desc_lbl2)

        cmd_tipo  = s.get('comando_tipo', '')
        cmd_valor = s.get('comando_valor', '')
        if cmd_tipo == 'alt_tecla' and cmd_valor:
            atalho_txt = f"atalho: <b>alt+{cmd_valor}</b>"
        elif cmd_tipo == 'shortcut' and cmd_valor:
            atalho_txt = f"atalho: <b>{cmd_valor}</b>"
        else:
            atalho_txt = None
        if atalho_txt:
            atalho_lbl = QLabel(atalho_txt)
            atalho_lbl.setTextFormat(Qt.TextFormat.RichText)
            atalho_lbl.setStyleSheet("font-family: 'Instrument Sans'; font-size: 11px; font-weight: 400; color: #88C22B; background: transparent; border: none;")
            text_col.addWidget(atalho_lbl)

        text_col.addStretch()
        outer.addLayout(text_col, stretch=1)

        # coluna direita
        svg_del_c = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2 3.99998H3.33333M3.33333 3.99998H14M3.33333 3.99998L3.33333 13.3333C3.33333 13.6869 3.47381 14.0261 3.72386 14.2761C3.97391 14.5262 4.31304 14.6666 4.66667 14.6666H11.3333C11.687 14.6666 12.0261 14.5262 12.2761 14.2761C12.5262 14.0261 12.6667 13.6869 12.6667 13.3333V3.99998M5.33333 3.99998V2.66665C5.33333 2.31302 5.47381 1.97389 5.72386 1.72384C5.97391 1.47379 6.31304 1.33331 6.66667 1.33331H9.33333C9.68696 1.33331 10.0261 1.47379 10.2761 1.72384C10.5262 1.97389 10.6667 2.31302 10.6667 2.66665V3.99998" stroke="#900B09" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
        svg_edit_c = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3.33333 12.6667H4.28333L10.8 6.15L9.85 5.2L3.33333 11.7167V12.6667ZM2 14V11.1667L10.8 2.38333C10.9333 2.26111 11.0806 2.16667 11.2417 2.1C11.4028 2.03333 11.5722 2 11.75 2C11.9278 2 12.1 2.03333 12.2667 2.1C12.4333 2.16667 12.5778 2.26667 12.7 2.4L13.6167 3.33333C13.75 3.45556 13.8472 3.6 13.9083 3.76667C13.9694 3.93333 14 4.1 14 4.26667C14 4.44444 13.9694 4.61389 13.9083 4.775C13.8472 4.93611 13.75 5.08333 13.6167 5.21667L4.83333 14H2ZM10.3167 5.68333L9.85 5.2L10.8 6.15L10.3167 5.68333Z" fill="#1D1B20"/></svg>"""

        if apenas_meus:
            right_col = QVBoxLayout(); right_col.setContentsMargins(0,0,0,0); right_col.setSpacing(6)

            # toggle no topo
            ativo_atual = [s.get('ativo', True)]
            toggle = ToggleSwitch(checked=ativo_atual[0])
            def _on_toggle(checked, sid=s.get('id'), aa=ativo_atual):
                aa[0] = checked
                threading.Thread(target=lambda: self.firebase.update_atalho_ativo(sid, checked), daemon=True).start()
            toggle.toggled.connect(_on_toggle)
            right_col.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignRight)
            right_col.addStretch()

            # botões lado a lado embaixo
            btns_row = QHBoxLayout(); btns_row.setContentsMargins(0,0,0,0); btns_row.setSpacing(4)
            btn_edit = QPushButton(); btn_edit.setIcon(create_svg_icon(svg_edit_c, 16)); btn_edit.setIconSize(QSize(16,16)); btn_edit.setFixedSize(24,24)
            btn_edit.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;}QPushButton:hover{background:rgba(0,0,0,0.06);}")
            btn_del = QPushButton(); btn_del.setIcon(create_svg_icon(svg_del_c, 16)); btn_del.setIconSize(QSize(16,16)); btn_del.setFixedSize(24,24)
            btn_del.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;}QPushButton:hover{background:rgba(144,11,9,0.08);}")
            btn_edit.clicked.connect(lambda _, atl=s: self.show_edit_atalho_overlay(atl))
            btn_del.clicked.connect(lambda _, atl=s: self.delete_atalho(atl))
            btns_row.addWidget(btn_edit); btns_row.addWidget(btn_del)
            right_col.addLayout(btns_row)
            outer.addLayout(right_col)
        else:
            # aba do setor: mostrar status + criador
            right_col = QVBoxLayout(); right_col.setContentsMargins(0,0,0,0); right_col.setSpacing(0)
            ativo = s.get('ativo', True)
            status_lbl = QLabel("ativado" if ativo else "desativado")
            status_lbl.setStyleSheet(f"font-family:'Instrument Sans'; font-size:11px; color:{'#499714' if ativo else '#909090'}; background:transparent; border:none;")
            right_col.addWidget(status_lbl, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            right_col.addStretch()
            nome_criador = self.firebase.get_user_nome(s['usuario_id'])
            criado_por = QLabel(f"Criado por: {nome_criador}")
            criado_por.setStyleSheet("font-family:'Instrument Sans'; font-size:10px; color:black; background:transparent; border:none;")
            right_col.addWidget(criado_por, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            outer.addLayout(right_col)

        return card

    def show_add_overlay(self):
        if getattr(self, '_current_tab', MainMenu._last_tab) == 'atalhos':
            self.show_add_atalho_overlay()
            return
        self.overlay_widget = OverlayDialog(self)
        self.overlay_widget._block_outside_close = True

        title = QLabel("Criação de template")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:16px; font-weight:bold; color:black; padding:0px 0px 15px 0px;")
        self.overlay_widget.add_content(title)

        def styled_input(ph):
            w = QLineEdit(); w.setPlaceholderText(ph)
            w.setStyleSheet("""
                QLineEdit { padding:10px; border:2px solid #C2C0B6; border-radius:8px; font-size:13px; background:transparent; color:black; }
                QLineEdit:focus { border:2px solid #B97E88; }
                QLineEdit::placeholder { color:#999; }
            """)
            return w

        self.tpl_titulo  = styled_input("Título do template")
        self.tpl_atalho  = styled_input("Atalho (opcional, ex: otb)")
        self.overlay_widget.add_content(self.tpl_titulo)
        self.overlay_widget.add_content(self.tpl_atalho)

        frame = QFrame()
        frame.setStyleSheet("QFrame { border:2px solid #C2C0B6; border-radius:8px; background:transparent; } QFrame:focus-within { border:2px solid #B97E88; }")
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
        ok = self.firebase.add_template(titulo, conteudo, atalho,
                                         self.user_data['uid'], self.user_data['setor'])
        if ok:
            self.overlay_widget.close()
            self._notification = NotificationWidget('✓ Template criado!')
            self._notification.show()
            QTimer.singleShot(100, self.show_templates_tab)
        else:
            QMessageBox.critical(self, "Erro", "Falha ao salvar no Firebase. Verifique sua conexão.")

    def show_add_atalho_overlay(self, atl_existente=None):
        editando = atl_existente is not None
        self.overlay_widget = OverlayDialog(self)
        self.overlay_widget._block_outside_close = True

        title = QLabel("Editar Atalho" if editando else "Criação de Atalho")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:bold; color:black; padding:0px 0px 8px 0px;")
        self.overlay_widget.add_content(title)

        self.atl_titulo = QLineEdit()
        self.atl_titulo.setPlaceholderText("Título do atalho")
        self.atl_titulo.setStyleSheet("""
            QLineEdit { padding:10px; border:2px solid #C2C0B6; border-radius:8px; font-size:13px; background:transparent; color:black; }
            QLineEdit:focus { border:2px solid #B97E88; }
            QLineEdit::placeholder { color:#999; }
        """)
        if editando:
            self.atl_titulo.setText(atl_existente.get('titulo', ''))
        self.overlay_widget.add_content(self.atl_titulo)

        # dropdown "Escolha o tipo de comando"
        svg_chevron_s = """<svg width="12" height="12" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2.5 5L7 9.5L11.5 5" stroke="#1E1E1E" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
        self._atl_tipo_expanded = False
        self._atl_tipo_selecionado = None

        tipo_row = QWidget()
        tipo_row.setStyleSheet("background:transparent;")
        tipo_row_layout = QHBoxLayout(tipo_row)
        tipo_row_layout.setContentsMargins(0, 0, 0, 0); tipo_row_layout.setSpacing(4)
        lbl_tipo = QLabel("Escolha o tipo de comando para ativar o atalho")
        lbl_tipo.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
        self._btn_tipo_chevron = QPushButton()
        self._btn_tipo_chevron.setIcon(create_svg_icon(svg_chevron_s, 12))
        self._btn_tipo_chevron.setIconSize(QSize(12, 12))
        self._btn_tipo_chevron.setFixedSize(20, 20)
        self._btn_tipo_chevron.setStyleSheet("QPushButton{background:transparent;border:none;}")
        tipo_row_layout.addWidget(lbl_tipo)
        tipo_row_layout.addWidget(self._btn_tipo_chevron)
        tipo_row_layout.addStretch()

        self._atl_tipo_container = QWidget()
        self._atl_tipo_container.setStyleSheet("""
            QWidget { background:#BAB9AD; border-radius:8px; }
        """)
        self._atl_tipo_container.setVisible(False)
        atl_tipo_layout = QVBoxLayout(self._atl_tipo_container)
        atl_tipo_layout.setContentsMargins(8, 6, 8, 6); atl_tipo_layout.setSpacing(2)

        # container que aparece após selecionar o tipo
        self._atl_tipo_conteudo = QWidget()
        self._atl_tipo_conteudo.setStyleSheet("background:transparent;")
        self._atl_tipo_conteudo.setVisible(False)
        self._atl_tipo_conteudo_layout = QVBoxLayout(self._atl_tipo_conteudo)
        self._atl_tipo_conteudo_layout.setContentsMargins(0, 4, 0, 0)
        self._atl_tipo_conteudo_layout.setSpacing(6)

        for opcao in ["Alt + tecla", "Atalho como dos templates"]:
            btn_opcao = QPushButton(opcao)
            btn_opcao.setStyleSheet("""
                QPushButton { font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent;
                              border:none; text-align:left; padding:5px 6px; border-radius:4px; }
                QPushButton:hover { background:rgba(0,0,0,0.08); }
            """)
            btn_opcao.setCursor(Qt.CursorShape.PointingHandCursor)

            def _on_opcao(checked=False, op=opcao):
                self._atl_tipo_selecionado = op
                _toggle_tipo()  # fechar dropdown
                # limpar conteúdo anterior
                while self._atl_tipo_conteudo_layout.count():
                    item = self._atl_tipo_conteudo_layout.takeAt(0)
                    if item.widget(): item.widget().deleteLater()
                if op == "Alt + tecla":
                    row = QHBoxLayout(); row.setSpacing(6); row.setContentsMargins(0,0,0,0)
                    lbl_cmd = QLabel("Comando:")
                    lbl_cmd.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
                    lbl_alt = QLabel("Alt")
                    lbl_alt.setFixedHeight(36)
                    lbl_alt.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    lbl_alt.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:2px solid #C2C0B6; border-radius:8px; padding:0 10px;")
                    lbl_plus = QLabel("+")
                    lbl_plus.setStyleSheet("font-family:'Inter'; font-size:13px; color:#1D1B20; background:transparent; border:none;")
                    self.atl_teclas = QLineEdit()
                    self.atl_teclas.setPlaceholderText("Uma ou duas teclas, separadas por vírgula")
                    self.atl_teclas.setStyleSheet("""
                        QLineEdit { padding:8px 10px; border:2px solid #C2C0B6; border-radius:8px; font-size:12px; background:transparent; color:black; }
                        QLineEdit:focus { border:2px solid #B97E88; }
                    """)
                    row.addWidget(lbl_cmd)
                    row.addWidget(lbl_alt)
                    row.addWidget(lbl_plus)
                    row.addWidget(self.atl_teclas, stretch=1)
                    rw = QWidget(); rw.setStyleSheet("background:transparent;"); rw.setLayout(row)
                    self._atl_tipo_conteudo_layout.addWidget(rw)
                else:
                    row = QHBoxLayout(); row.setSpacing(6); row.setContentsMargins(0,0,0,0)
                    lbl_cmd = QLabel("Comando:")
                    lbl_cmd.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
                    self.atl_shortcut = QLineEdit()
                    self.atl_shortcut.setPlaceholderText("Ex: itb, gp...")
                    self.atl_shortcut.setStyleSheet("""
                        QLineEdit { padding:8px 10px; border:2px solid #C2C0B6; border-radius:8px; font-size:12px; background:transparent; color:black; }
                        QLineEdit:focus { border:2px solid #B97E88; }
                    """)
                    row.addWidget(lbl_cmd)
                    row.addWidget(self.atl_shortcut, stretch=1)
                    rw = QWidget(); rw.setStyleSheet("background:transparent;"); rw.setLayout(row)
                    self._atl_tipo_conteudo_layout.addWidget(rw)
                self._atl_tipo_conteudo.setVisible(True)

            btn_opcao.clicked.connect(_on_opcao)
            atl_tipo_layout.addWidget(btn_opcao)

        def _toggle_tipo():
            self._atl_tipo_expanded = not self._atl_tipo_expanded
            from PyQt6.QtGui import QTransform, QIcon
            px = create_svg_icon(svg_chevron_s, 12).pixmap(12, 12)
            t = QTransform().rotate(180 if self._atl_tipo_expanded else 0)
            self._btn_tipo_chevron.setIcon(QIcon(px.transformed(t)))
            self._atl_tipo_container.setVisible(self._atl_tipo_expanded)

        tipo_row.mousePressEvent = lambda e: _toggle_tipo()
        tipo_row.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_tipo_chevron.clicked.connect(_toggle_tipo)

        self.overlay_widget.add_content(tipo_row)
        self.overlay_widget.add_content(self._atl_tipo_container)
        self.overlay_widget.add_content(self._atl_tipo_conteudo)

        # campo de ações
        acoes_frame = QFrame()
        acoes_frame.setStyleSheet("QFrame { border:2px solid #C2C0B6; border-radius:8px; background:transparent; }")
        acoes_frame.setMinimumHeight(200)
        acoes_layout = QVBoxLayout(acoes_frame)
        acoes_layout.setContentsMargins(8, 8, 8, 8); acoes_layout.setSpacing(6)
        self._acoes_lista_layout = QVBoxLayout(); self._acoes_lista_layout.setSpacing(4)
        self._acoes_lista_layout.setContentsMargins(0,0,0,0)
        acoes_layout.addLayout(self._acoes_lista_layout)

        # dropdown de adicionar ação
        self._add_acao_expanded = False
        svg_chev_a = """<svg width="12" height="12" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2.5 5L7 9.5L11.5 5" stroke="#555" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

        add_row_w = QWidget(); add_row_w.setStyleSheet("background:transparent;")
        add_row_l = QHBoxLayout(add_row_w); add_row_l.setContentsMargins(0,0,0,0); add_row_l.setSpacing(4)
        btn_add_acao = QPushButton("+ Adicionar ação")
        btn_add_acao.setStyleSheet("""
            QPushButton { font-family:'Inter'; font-size:12px; color:#555; background:transparent;
                          border:1px dashed #C2C0B6; border-radius:6px; padding:4px 8px; }
            QPushButton:hover { background:rgba(0,0,0,0.05); }
        """)
        btn_add_acao.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_acao.setFixedWidth(130)
        add_row_l.addWidget(btn_add_acao); add_row_l.addStretch()

        # container do dropdown de tipos de ação
        self._add_acao_container = QWidget()
        self._add_acao_container.setStyleSheet("QWidget { background:#BAB9AD; border-radius:8px; }")
        self._add_acao_container.setVisible(False)
        add_acao_opts = QVBoxLayout(self._add_acao_container)
        add_acao_opts.setContentsMargins(8, 6, 8, 6); add_acao_opts.setSpacing(2)

        btn_click_esq = QPushButton("Clique do mouse")
        btn_click_esq.setStyleSheet("""
            QPushButton { font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent;
                          border:none; text-align:left; padding:5px 6px; border-radius:4px; }
            QPushButton:hover { background:rgba(0,0,0,0.08); }
        """)
        btn_click_esq.setCursor(Qt.CursorShape.PointingHandCursor)
        add_acao_opts.addWidget(btn_click_esq)

        btn_esperar = QPushButton("Esperar")
        btn_esperar.setStyleSheet("""
            QPushButton { font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent;
                          border:none; text-align:left; padding:5px 6px; border-radius:4px; }
            QPushButton:hover { background:rgba(0,0,0,0.08); }
        """)
        btn_esperar.setCursor(Qt.CursorShape.PointingHandCursor)
        add_acao_opts.addWidget(btn_esperar)

        def _iniciar_esperar():
            self._add_acao_expanded = False
            self._add_acao_container.setVisible(False)
            _mostrar_editor_esperar()

        def _mostrar_editor_esperar(wrapper_existente=None):
            wrapper = QWidget(); wrapper.setStyleSheet("background:transparent;")
            wl = QVBoxLayout(wrapper); wl.setContentsMargins(0,0,0,2); wl.setSpacing(2)

            card = QWidget(); card.setStyleSheet("background:#C2C0B6; border-radius:6px;")
            cl = QHBoxLayout(card); cl.setContentsMargins(8,6,8,6); cl.setSpacing(6)

            lbl_pre = QLabel("Esperar")
            lbl_pre.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")

            spin_ms = QLineEdit()
            spin_ms.setFixedWidth(60)
            spin_ms.setPlaceholderText("ms")
            spin_ms.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin_ms.setStyleSheet("QLineEdit{font-family:'Inter';font-size:12px;color:#1D1B20;background:white;border:1px solid #aaa;border-radius:4px;padding:2px;}")

            lbl_ms = QLabel("ms")
            lbl_ms.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")

            lbl_hint_ms = QLabel("1000 = 1 segundo")
            lbl_hint_ms.setStyleSheet("font-family:'Inter'; font-size:10px; color:#666; background:transparent; border:none;")

            btn_ok2 = QPushButton(); btn_ok2.setIcon(create_svg_icon(svg_check, 13)); btn_ok2.setIconSize(QSize(13,13)); btn_ok2.setFixedSize(22,22)
            btn_ok2.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(73,151,20,0.1);border-radius:4px;}")
            btn_cx2 = QPushButton(); btn_cx2.setIcon(create_svg_icon(svg_xmark, 13)); btn_cx2.setIconSize(QSize(13,13)); btn_cx2.setFixedSize(22,22)
            btn_cx2.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(144,11,9,0.1);border-radius:4px;}")

            def _confirmar_esperar():
                try: ms = max(1, int(spin_ms.text()))
                except: return
                card_conf = _criar_card_esperar(ms)
                idx = self._acoes_lista_layout.indexOf(wrapper)
                wrapper.setParent(None); wrapper.deleteLater()
                if wrapper_existente:
                    try: wrapper_existente.setParent(None); wrapper_existente.deleteLater()
                    except RuntimeError: pass
                self._acoes_lista_layout.insertWidget(max(0, idx), card_conf)

            def _cancelar_esperar():
                if wrapper_existente: wrapper_existente.setVisible(True)
                wrapper.setParent(None); wrapper.deleteLater()

            def _key_ms(e):
                if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter): _confirmar_esperar()
                elif e.key() == Qt.Key.Key_Escape: _cancelar_esperar()
                else: QLineEdit.keyPressEvent(spin_ms, e)
            spin_ms.keyPressEvent = _key_ms

            btn_ok2.clicked.connect(_confirmar_esperar)
            btn_cx2.clicked.connect(_cancelar_esperar)

            cl.addWidget(lbl_pre); cl.addWidget(spin_ms); cl.addWidget(lbl_ms); cl.addStretch()
            cl.addWidget(btn_ok2); cl.addWidget(btn_cx2)
            wl.addWidget(card)
            wl.addWidget(lbl_hint_ms)

            if wrapper_existente:
                wrapper_existente.setVisible(False)
                idx = self._acoes_lista_layout.indexOf(wrapper_existente)
                self._acoes_lista_layout.insertWidget(idx, wrapper)
            else:
                self._acoes_lista_layout.addWidget(wrapper)
            spin_ms.setFocus()

        def _criar_card_esperar(ms):
            wrapper = QWidget(); wrapper.setStyleSheet("background:transparent;")
            wrapper.setProperty('acao_data', json.dumps({'tipo': 'esperar', 'ms': ms}))
            wl = QHBoxLayout(wrapper); wl.setContentsMargins(0,0,0,0); wl.setSpacing(4)
            card = QWidget(); card.setStyleSheet("background:#C2C0B6; border-radius:6px;")
            cl = QHBoxLayout(card); cl.setContentsMargins(8,6,8,6); cl.setSpacing(6)
            desc = QLabel(f"Esperar {ms} ms")
            desc.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
            cl.addWidget(desc); cl.addStretch()
            btn_ed = QPushButton(); btn_ed.setIcon(create_svg_icon(svg_edit_a, 14)); btn_ed.setIconSize(QSize(14,14)); btn_ed.setFixedSize(22,22)
            btn_ed.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(0,0,0,0.06);border-radius:4px;}")
            btn_rm = QPushButton(); btn_rm.setIcon(create_svg_icon(svg_del_a, 14)); btn_rm.setIconSize(QSize(14,14)); btn_rm.setFixedSize(22,22)
            btn_rm.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(144,11,9,0.08);border-radius:4px;}")
            btn_ed.clicked.connect(lambda: _mostrar_editor_esperar(wrapper))
            btn_rm.clicked.connect(lambda: (wrapper.setParent(None), wrapper.deleteLater()))
            cl.addWidget(btn_ed); cl.addWidget(btn_rm)
            wl.addWidget(card)
            return wrapper

        btn_esperar.clicked.connect(_iniciar_esperar)

        def _toggle_add_acao():
            self._add_acao_expanded = not self._add_acao_expanded
            self._add_acao_container.setVisible(self._add_acao_expanded)

        btn_add_acao.clicked.connect(_toggle_add_acao)

        svg_check = """<svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5.3 8.1L8.825 4.575L8.125 3.875L5.3 6.7L3.875 5.275L3.175 5.975L5.3 8.1ZM2.5 10.5C2.225 10.5 1.98958 10.4021 1.79375 10.2063C1.59792 10.0104 1.5 9.775 1.5 9.5V2.5C1.5 2.225 1.59792 1.98958 1.79375 1.79375C1.98958 1.59792 2.225 1.5 2.5 1.5H9.5C9.775 1.5 10.0104 1.59792 10.2063 1.79375C10.4021 1.98958 10.5 2.225 10.5 2.5V9.5C10.5 9.775 10.4021 10.0104 10.2063 10.2063C10.0104 10.4021 9.775 10.5 9.5 10.5H2.5ZM2.5 9.5H9.5V2.5H2.5V9.5Z" fill="#499714"/></svg>"""
        svg_xmark  = """<svg width="12" height="12" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M9 3L3 9M3 3L9 9" stroke="#900B09" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
        svg_del_a  = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2 3.99998H3.33333M3.33333 3.99998H14M3.33333 3.99998L3.33333 13.3333C3.33333 13.6869 3.47381 14.0261 3.72386 14.2761C3.97391 14.5262 4.31304 14.6666 4.66667 14.6666H11.3333C11.687 14.6666 12.0261 14.5262 12.2761 14.2761C12.5262 14.0261 12.6667 13.6869 12.6667 13.3333V3.99998M5.33333 3.99998V2.66665C5.33333 2.31302 5.47381 1.97389 5.72386 1.72384C5.97391 1.47379 6.31304 1.33331 6.66667 1.33331H9.33333C9.68696 1.33331 10.0261 1.47379 10.2761 1.72384C10.5262 1.97389 10.6667 2.31302 10.6667 2.66665V3.99998" stroke="#900B09" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
        svg_edit_a = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3.33333 12.6667H4.28333L10.8 6.15L9.85 5.2L3.33333 11.7167V12.6667ZM2 14V11.1667L10.8 2.38333C10.9333 2.26111 11.0806 2.16667 11.2417 2.1C11.4028 2.03333 11.5722 2 11.75 2C11.9278 2 12.1 2.03333 12.2667 2.1C12.4333 2.16667 12.5778 2.26667 12.7 2.4L13.6167 3.33333C13.75 3.45556 13.8472 3.6 13.9083 3.76667C13.9694 3.93333 14 4.1 14 4.26667C14 4.44444 13.9694 4.61389 13.9083 4.775C13.8472 4.93611 13.75 5.08333 13.6167 5.21667L4.83333 14H2ZM10.3167 5.68333L9.85 5.2L10.8 6.15L10.3167 5.68333Z" fill="#1D1B20"/></svg>"""

        def _iniciar_captura_click():
            self._add_acao_expanded = False
            self._add_acao_container.setVisible(False)
            self.hide()
            screen = QApplication.primaryScreen().geometry()
            self._capture_overlay = QWidget(None)
            self._capture_overlay.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
            self._capture_overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self._capture_overlay.setGeometry(screen)
            def _paint(ev):
                p = QPainter(self._capture_overlay)
                p.fillRect(self._capture_overlay.rect(), QColor(0, 0, 0, 80)); p.end()
            self._capture_overlay.paintEvent = _paint
            lbl = QLabel("Clique onde deseja executar a ação", self._capture_overlay)
            lbl.setStyleSheet("color:white; font-family:'Inter'; font-size:14px; background:transparent;")
            lbl.adjustSize(); lbl.move(screen.width()//2 - lbl.width()//2, 30)
            def _on_click(ev):
                gpos = ev.globalPosition().toPoint()
                x, y = gpos.x(), gpos.y()
                self._capture_overlay.close(); self._capture_overlay = None
                self.show(); self.raise_()
                _mostrar_editor_acao(x, y)
            self._capture_overlay.mousePressEvent = _on_click
            self._capture_overlay.setCursor(Qt.CursorShape.CrossCursor)
            self._capture_overlay.show()

        def _mostrar_editor_acao(x, y, wrapper_existente=None, dados_existentes=None):
            # extrair dados existentes para pré-preencher
            op_inicial  = dados_existentes.get('botao', None) if dados_existentes else None
            qtd_inicial = dados_existentes.get('qtd', 1)      if dados_existentes else 1

            wrapper = QWidget(); wrapper.setStyleSheet("background:transparent;")
            wl = QVBoxLayout(wrapper); wl.setContentsMargins(0,0,0,2); wl.setSpacing(2)

            card = QWidget(); card.setStyleSheet("background:#C2C0B6; border-radius:6px;")
            cl = QHBoxLayout(card); cl.setContentsMargins(8,6,8,6); cl.setSpacing(4)

            lbl_pre = QLabel("Clicar com o botão")
            lbl_pre.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")

            btn_dd = QPushButton("▾")
            btn_dd.setFixedHeight(26); btn_dd.setMinimumWidth(40)
            btn_dd.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#1D1B20;background:white;border:1px solid #aaa;border-radius:4px;padding:0 6px;}QPushButton:hover{background:#eee;}")
            btn_dd.setCursor(Qt.CursorShape.PointingHandCursor)

            dd_cont = QWidget(); dd_cont.setStyleSheet("QWidget{background:#BAB9AD;border-radius:6px;}")
            dd_cont.setVisible(False)
            dc_l = QVBoxLayout(dd_cont); dc_l.setContentsMargins(6,4,6,4); dc_l.setSpacing(2)

            qtd_w = QWidget(); qtd_w.setStyleSheet("background:transparent;")
            qtd_l = QHBoxLayout(qtd_w); qtd_l.setContentsMargins(0,0,0,0); qtd_l.setSpacing(4)
            spin = QLineEdit(""); spin.setFixedWidth(32); spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setStyleSheet("QLineEdit{font-family:'Inter';font-size:12px;color:#1D1B20;background:white;border:1px solid #aaa;border-radius:4px;padding:2px;}")
            lbl_vez = QLabel("vez.")
            lbl_vez.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
            def _upd_vez():
                try: lbl_vez.setText("vez." if int(spin.text()) == 1 else "vezes.")
                except: lbl_vez.setText("vez.")
            spin.textChanged.connect(_upd_vez)
            qtd_l.addWidget(spin); qtd_l.addWidget(lbl_vez)
            qtd_w.setVisible(False)

            lbl_hint = QLabel("Digite quantos cliques deseja dar.")
            lbl_hint.setStyleSheet("font-family:'Inter'; font-size:10px; color:#666; background:transparent; border:none;")
            lbl_hint.setVisible(False)

            botao_sel = [op_inicial]
            opcoes = ['E', 'D', 'do meio']
            idx_hover = [-1]
            botoes_dd = []

            # pré-preencher se editando
            if op_inicial:
                btn_dd.setText(op_inicial)
                qtd_w.setVisible(op_inicial == 'E')
                lbl_hint.setVisible(op_inicial == 'E')
                if op_inicial == 'E':
                    spin.setText(str(qtd_inicial))
                    _upd_vez()

            def _atualizar_highlight():
                for i, b in enumerate(botoes_dd):
                    if i == idx_hover[0]:
                        b.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#1D1B20;background:rgba(0,0,0,0.12);border:none;text-align:left;padding:4px 6px;border-radius:4px;}")
                    else:
                        b.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#1D1B20;background:transparent;border:none;text-align:left;padding:4px 6px;border-radius:4px;}QPushButton:hover{background:rgba(0,0,0,0.08);}")

            def _sel_botao(op):
                btn_dd.setText(op); dd_cont.setVisible(False); botao_sel[0] = op
                qtd_w.setVisible(op == 'E'); lbl_hint.setVisible(op == 'E')
                if op == 'E': QTimer.singleShot(50, spin.setFocus)

            def _key_dd(ev):
                # se o spin está com foco, não interceptar — ele tem seu próprio keyPressEvent
                if qtd_w.isVisible() and spin.hasFocus():
                    return
                if dd_cont.isVisible():
                    if ev.key() == Qt.Key.Key_Down:
                        idx_hover[0] = min(idx_hover[0] + 1, len(opcoes) - 1)
                        _atualizar_highlight()
                    elif ev.key() == Qt.Key.Key_Up:
                        idx_hover[0] = max(idx_hover[0] - 1, 0)
                        _atualizar_highlight()
                    elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        if idx_hover[0] >= 0:
                            op = opcoes[idx_hover[0]]
                            _sel_botao(op)
                            if op != 'E':
                                _confirmar()
                    elif ev.key() == Qt.Key.Key_Escape:
                        dd_cont.setVisible(False)
                else:
                    if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                        idx_hover[0] = 0
                        dd_cont.setVisible(True)
                        _atualizar_highlight()
                        btn_dd.setFocus()
                    elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        if botao_sel[0] and botao_sel[0] != 'E':
                            _confirmar()
                        elif botao_sel[0] == 'E':
                            QTimer.singleShot(50, spin.setFocus)
                        else:
                            idx_hover[0] = 0
                            dd_cont.setVisible(True)
                            _atualizar_highlight()
                            btn_dd.setFocus()
                    elif ev.key() == Qt.Key.Key_Escape:
                        _cancelar()

            btn_dd.keyPressEvent = _key_dd
            btn_dd.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

            for op in opcoes:
                b = QPushButton(op)
                b.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#1D1B20;background:transparent;border:none;text-align:left;padding:4px 6px;border-radius:4px;}QPushButton:hover{background:rgba(0,0,0,0.08);}")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _, o=op: _sel_botao(o))
                dc_l.addWidget(b)
                botoes_dd.append(b)

            btn_dd.clicked.connect(lambda: dd_cont.setVisible(not dd_cont.isVisible()))

            svg_pin = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><g clip-path="url(#clip0_103_43)"><path d="M14 6.66669C14 11.3334 8 15.3334 8 15.3334C8 15.3334 2 11.3334 2 6.66669C2 5.07539 2.63214 3.54926 3.75736 2.42405C4.88258 1.29883 6.4087 0.666687 8 0.666687C9.5913 0.666687 11.1174 1.29883 12.2426 2.42405C13.3679 3.54926 14 5.07539 14 6.66669Z" stroke="#1E1E1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 8.66669C9.10457 8.66669 10 7.77126 10 6.66669C10 5.56212 9.10457 4.66669 8 4.66669C6.89543 4.66669 6 5.56212 6 6.66669C6 7.77126 6.89543 8.66669 8 8.66669Z" stroke="#1E1E1E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></g><defs><clipPath id="clip0_103_43"><rect width="16" height="16" fill="white"/></clipPath></defs></svg>"""

            btn_pin = QPushButton(); btn_pin.setIcon(create_svg_icon(svg_pin, 14)); btn_pin.setIconSize(QSize(14,14)); btn_pin.setFixedSize(22,22)
            btn_pin.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(0,0,0,0.07);border-radius:4px;}")
            btn_pin.setCursor(Qt.CursorShape.PointingHandCursor)

            # tooltip customizado com delay
            _pin_tooltip = QLabel("Recapturar local do clique")
            _pin_tooltip.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            _pin_tooltip.setStyleSheet("QLabel{background:#1D1B20;color:white;font-family:'Inter';font-size:11px;padding:4px 8px;border-radius:4px;}")
            _pin_tooltip.adjustSize()
            _pin_timer = QTimer(); _pin_timer.setSingleShot(True)

            def _show_pin_tooltip():
                pos = QCursor.pos()
                _pin_tooltip.move(pos.x() + 10, pos.y() + 16)
                _pin_tooltip.show()
            def _hide_pin_tooltip():
                _pin_timer.stop(); _pin_tooltip.hide()

            def _enter_pin(e):
                if not _pin_timer.isActive():
                    _pin_timer.start(300)
            def _leave_pin(e):
                _hide_pin_tooltip()

            btn_pin.enterEvent = _enter_pin
            btn_pin.leaveEvent = _leave_pin

            _pin_timer.timeout.connect(_show_pin_tooltip)

            # recapturar: fecha overlay, captura novo ponto, reabre editor
            def _recapturar(we=wrapper_existente, op=botao_sel, qtd_spin=spin):
                _hide_pin_tooltip()
                self.hide()
                screen = QApplication.primaryScreen().geometry()
                self._capture_overlay = QWidget(None)
                self._capture_overlay.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
                self._capture_overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                self._capture_overlay.setGeometry(screen)
                def _paint2(ev):
                    p = QPainter(self._capture_overlay)
                    p.fillRect(self._capture_overlay.rect(), QColor(0,0,0,80)); p.end()
                self._capture_overlay.paintEvent = _paint2
                lbl2 = QLabel("Clique onde deseja executar a ação", self._capture_overlay)
                lbl2.setStyleSheet("color:white; font-family:'Inter'; font-size:14px; background:transparent;")
                lbl2.adjustSize(); lbl2.move(screen.width()//2 - lbl2.width()//2, 30)
                def _on_recapture(ev, _we=we, _op=op[0], _qtd=qtd_spin.text()):
                    gpos = ev.globalPosition().toPoint()
                    nx, ny = gpos.x(), gpos.y()
                    self._capture_overlay.close(); self._capture_overlay = None
                    self.show(); self.raise_()
                    try:
                        qtd_val = max(1, int(_qtd))
                    except:
                        qtd_val = 1
                    dados = {'botao': _op, 'qtd': qtd_val} if _op else None
                    # remover o editor atual (wrapper) antes de reabrir
                    wrapper.setParent(None); wrapper.deleteLater()
                    _mostrar_editor_acao(nx, ny, _we, dados)
                    self._notification = NotificationWidget('📍 Posição recapturada!')
                    self._notification.show()
                self._capture_overlay.mousePressEvent = _on_recapture
                self._capture_overlay.setCursor(Qt.CursorShape.CrossCursor)
                self._capture_overlay.show()

            btn_pin.clicked.connect(_recapturar)

            btn_ok = QPushButton(); btn_ok.setIcon(create_svg_icon(svg_check, 13)); btn_ok.setIconSize(QSize(13,13)); btn_ok.setFixedSize(22,22)
            btn_ok.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(73,151,20,0.1);border-radius:4px;}")
            btn_cx = QPushButton(); btn_cx.setIcon(create_svg_icon(svg_xmark, 13)); btn_cx.setIconSize(QSize(13,13)); btn_cx.setFixedSize(22,22)
            btn_cx.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(144,11,9,0.1);border-radius:4px;}")

            def _confirmar():
                op = botao_sel[0]
                if not op: return
                qtd = 1
                if op == 'E':
                    try: qtd = max(1, int(spin.text()))
                    except: qtd = 1
                card_conf = _criar_card_conf(x, y, op, qtd)
                idx = self._acoes_lista_layout.indexOf(wrapper)
                wrapper.setParent(None); wrapper.deleteLater()
                if wrapper_existente:
                    try: wrapper_existente.setParent(None); wrapper_existente.deleteLater()
                    except RuntimeError: pass
                self._acoes_lista_layout.insertWidget(max(0, idx), card_conf)

            def _cancelar():
                if wrapper_existente: wrapper_existente.setVisible(True)
                wrapper.setParent(None); wrapper.deleteLater()

            def _key(ev):
                if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter): _confirmar()
                elif ev.key() == Qt.Key.Key_Escape: _cancelar()
                else: QLineEdit.keyPressEvent(spin, ev)
            spin.keyPressEvent = _key
            btn_ok.clicked.connect(_confirmar)
            btn_cx.clicked.connect(_cancelar)

            cl.addWidget(lbl_pre); cl.addWidget(btn_dd); cl.addWidget(qtd_w); cl.addStretch()
            cl.addWidget(btn_pin); cl.addWidget(btn_ok); cl.addWidget(btn_cx)
            wl.addWidget(card); wl.addWidget(dd_cont); wl.addWidget(lbl_hint)

            if wrapper_existente:
                wrapper_existente.setVisible(False)
                idx = self._acoes_lista_layout.indexOf(wrapper_existente)
                self._acoes_lista_layout.insertWidget(idx, wrapper)
            else:
                self._acoes_lista_layout.addWidget(wrapper)

            # abrir dropdown e focar automaticamente (só se não há dados pré-existentes)
            def _init_dropdown():
                if op_inicial:
                    return  # já pré-preenchido, não abrir dropdown
                idx_hover[0] = 0
                dd_cont.setVisible(True)
                _atualizar_highlight()
                self.activateWindow()
                self.raise_()
                btn_dd.setFocus(Qt.FocusReason.OtherFocusReason)
            QTimer.singleShot(100, _init_dropdown)

        def _criar_card_conf(x, y, op, qtd):
            if op == 'E':
                txt = f"Clicar com o botão E {qtd} {'vez' if qtd==1 else 'vezes'}."
            else:
                txt = f"Clicar com o botão {op}."
            wrapper = QWidget(); wrapper.setStyleSheet("background:transparent;")
            # guardar dados estruturados para execução
            wrapper.setProperty('acao_data', json.dumps({'tipo': 'click', 'botao': op, 'qtd': qtd, 'x': x, 'y': y}))
            wl = QHBoxLayout(wrapper); wl.setContentsMargins(0,0,0,0); wl.setSpacing(4)
            card = QWidget(); card.setStyleSheet("background:#C2C0B6; border-radius:6px;")
            cl = QHBoxLayout(card); cl.setContentsMargins(8,6,8,6); cl.setSpacing(6)
            desc = QLabel(txt); desc.setStyleSheet("font-family:'Inter'; font-size:12px; color:#1D1B20; background:transparent; border:none;")
            cl.addWidget(desc); cl.addStretch()
            btn_ed = QPushButton(); btn_ed.setIcon(create_svg_icon(svg_edit_a, 14)); btn_ed.setIconSize(QSize(14,14)); btn_ed.setFixedSize(22,22)
            btn_ed.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(0,0,0,0.06);border-radius:4px;}")
            btn_rm = QPushButton(); btn_rm.setIcon(create_svg_icon(svg_del_a, 14)); btn_rm.setIconSize(QSize(14,14)); btn_rm.setFixedSize(22,22)
            btn_rm.setStyleSheet("QPushButton{background:transparent;border:none;}QPushButton:hover{background:rgba(144,11,9,0.08);border-radius:4px;}")
            btn_ed.clicked.connect(lambda _=None, _x=x, _y=y, _w=wrapper, _op=op, _qtd=qtd: _mostrar_editor_acao(_x, _y, _w, {'botao': _op, 'qtd': _qtd}))
            btn_rm.clicked.connect(lambda: (wrapper.setParent(None), wrapper.deleteLater()))
            cl.addWidget(btn_ed); cl.addWidget(btn_rm)
            wl.addWidget(card)
            return wrapper

        btn_click_esq.clicked.connect(_iniciar_captura_click)

        acoes_layout.addWidget(add_row_w)
        acoes_layout.addWidget(self._add_acao_container)
        acoes_layout.addStretch()
        self.overlay_widget.add_content(acoes_frame)

        btns = QHBoxLayout(); btns.setSpacing(10); btns.addStretch()
        b_criar = QPushButton("Salvar" if editando else "Criar"); b_criar.setFixedWidth(90)
        b_criar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:white;background:#499714;border:none;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:#3d8010;}")
        b_criar.clicked.connect(lambda: self._salvar_atalho(atl_existente))
        b_cancel = QPushButton("Cancelar"); b_cancel.setFixedWidth(90)
        b_cancel.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:#499714;background:transparent;border:2px solid #499714;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:rgba(73,151,20,0.1);}")
        b_cancel.clicked.connect(self.overlay_widget.close)
        btns.addWidget(b_criar); btns.addWidget(b_cancel)
        bw = QWidget(); bw.setStyleSheet("background:transparent;"); bw.setLayout(btns)
        self.overlay_widget.add_content(bw)

        self.overlay_widget.show()

        # pré-preencher tipo e ações se editando
        if editando:
            cmd_tipo  = atl_existente.get('comando_tipo', '')
            cmd_valor = atl_existente.get('comando_valor', '')
            if cmd_tipo == 'alt_tecla':
                _on_opcao(op="Alt + tecla")
                if hasattr(self, 'atl_teclas'): self.atl_teclas.setText(cmd_valor)
            elif cmd_tipo == 'shortcut':
                _on_opcao(op="Atalho como dos templates")
                if hasattr(self, 'atl_shortcut'): self.atl_shortcut.setText(cmd_valor)
            # fechar o dropdown após selecionar
            self._atl_tipo_container.setVisible(False)
            self._atl_tipo_expanded = False
            from PyQt6.QtGui import QTransform, QIcon
            px = create_svg_icon(svg_chevron_s, 12).pixmap(12, 12)
            self._btn_tipo_chevron.setIcon(QIcon(px.transformed(QTransform().rotate(0))))
            # pré-carregar ações
            for acao in atl_existente.get('acoes', []):
                if isinstance(acao, dict) and acao.get('tipo') == 'click':
                    card = _criar_card_conf(acao['x'], acao['y'], acao['botao'], acao.get('qtd', 1))
                    self._acoes_lista_layout.addWidget(card)
                elif isinstance(acao, dict) and acao.get('tipo') == 'esperar':
                    card = _criar_card_esperar(acao['ms'])
                    self._acoes_lista_layout.addWidget(card)

    def _salvar_atalho(self, atl_existente=None):
        editando = atl_existente is not None
        titulo = getattr(self, 'atl_titulo', None)
        titulo = titulo.text().strip() if titulo else ''
        if not titulo:
            if hasattr(self, 'atl_titulo'): self.show_field_error(self.atl_titulo, "Este campo é obrigatório")
            return
        # tipo de comando
        tipo = getattr(self, '_atl_tipo_selecionado', None)
        if not tipo:
            self.show_field_error(self.atl_titulo, "Escolha o tipo de comando para ativar o atalho")
            return
        if tipo == 'Alt + tecla':
            cmd_tipo  = 'alt_tecla'
            cmd_valor = getattr(self, 'atl_teclas', None)
            cmd_valor = cmd_valor.text().strip() if cmd_valor else ''
            if not cmd_valor:
                if hasattr(self, 'atl_teclas'): self.show_field_error(self.atl_teclas, "Este campo é obrigatório")
                return
        elif tipo == 'Atalho como dos templates':
            cmd_tipo  = 'shortcut'
            cmd_valor = getattr(self, 'atl_shortcut', None)
            cmd_valor = cmd_valor.text().strip() if cmd_valor else ''
            if not cmd_valor:
                if hasattr(self, 'atl_shortcut'): self.show_field_error(self.atl_shortcut, "Este campo é obrigatório")
                return
        else:
            cmd_tipo  = ''
            cmd_valor = ''
        # coletar ações dos cards confirmados
        acoes = []
        layout = getattr(self, '_acoes_lista_layout', None)
        if layout:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    w = item.widget()
                    data = w.property('acao_data')
                    if data:
                        acoes.append(json.loads(data))
        if editando:
            ok = self.firebase.update_atalho(
                atl_existente['id'], titulo, cmd_tipo, cmd_valor, acoes
            )
            msg = '✓ Atalho atualizado!'
        else:
            ok = self.firebase.add_atalho(
                titulo, cmd_tipo, cmd_valor, acoes,
                self.user_data['uid'], self.user_data['setor']
            )
            msg = '✓ Atalho criado!'
        if ok:
            self.overlay_widget.close()
            self._notification = NotificationWidget(msg)
            self._notification.show()
            QTimer.singleShot(100, self.show_atalhos_tab)
        else:
            QMessageBox.critical(self, "Erro", "Falha ao salvar no Firebase.")

    def show_edit_overlay(self, t):
        self.overlay_widget = OverlayDialog(self)
        self._editing_id = t['id']

        title = QLabel("Editar template")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:16px; font-weight:bold; color:black; padding:0px 0px 15px 0px;")
        self.overlay_widget.add_content(title)

        def styled_input(ph):
            w = QLineEdit(); w.setPlaceholderText(ph)
            w.setStyleSheet("""
                QLineEdit { padding:10px; border:2px solid #C2C0B6; border-radius:8px; font-size:13px; background:transparent; color:black; }
                QLineEdit:focus { border:2px solid #B97E88; }
                QLineEdit::placeholder { color:#999; }
            """)
            return w

        self.tpl_titulo = styled_input("Título do template")
        self.tpl_titulo.setText(t['nome'])
        self.tpl_atalho = styled_input("Atalho (opcional, ex: otb)")
        self.tpl_atalho.setText(t.get('atalho', ''))
        self.overlay_widget.add_content(self.tpl_titulo)
        self.overlay_widget.add_content(self.tpl_atalho)

        frame = QFrame()
        frame.setStyleSheet("QFrame { border:2px solid #C2C0B6; border-radius:8px; background:transparent; } QFrame:focus-within { border:2px solid #B97E88; }")
        fl = QVBoxLayout(); fl.setContentsMargins(0,0,0,0)
        self.tpl_conteudo = QTextEdit()
        self.tpl_conteudo.setPlaceholderText("Conteúdo do template")
        self.tpl_conteudo.setStyleSheet("QTextEdit { padding:8px; border:none; font-size:13px; background:transparent; color:black; }")
        self.tpl_conteudo.setText(t['texto'])
        palette = self.tpl_conteudo.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, QColor("#999"))
        self.tpl_conteudo.setPalette(palette)
        fl.addWidget(self.tpl_conteudo); frame.setLayout(fl)
        self.overlay_widget.add_content(frame)
        self._overlay_frame = frame

        btns = QHBoxLayout(); btns.setSpacing(10); btns.addStretch()
        b_salvar = QPushButton("Salvar"); b_salvar.setFixedWidth(90)
        b_salvar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:white;background:#82414C;border:none;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:#6d3640;}")
        b_salvar.clicked.connect(self.save_edit_template)
        b_cancel = QPushButton("Cancelar"); b_cancel.setFixedWidth(90)
        b_cancel.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:#82414C;background:transparent;border:2px solid #82414C;border-radius:6px;padding:8px 16px;}QPushButton:hover{background:rgba(130,65,76,0.1);}")
        b_cancel.clicked.connect(self.overlay_widget.close)
        btns.addWidget(b_salvar); btns.addWidget(b_cancel)
        bw = QWidget(); bw.setLayout(btns)
        self.overlay_widget.add_content(bw)
        self.overlay_widget.show()

    def save_edit_template(self):
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

        ok = self.firebase.update_template(self._editing_id, titulo, conteudo, atalho)
        if ok:
            self.overlay_widget.close()
            self._notification = NotificationWidget('✓ Template atualizado!')
            self._notification.show()
            QTimer.singleShot(100, self.show_templates_tab)
        else:
            QMessageBox.critical(self, "Erro", "Falha ao salvar. Verifique sua conexão.")
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
            self._notification = NotificationWidget('✓ Template criado!')
            self._notification.show()
            QTimer.singleShot(100, self.show_templates_tab)
        else:
            QMessageBox.critical(self, "Erro", "Falha ao salvar no Firebase. Verifique sua conexão.")

    def edit_template(self, t):
        if self.add_window and self.add_window.isVisible():
            self.add_window.close()
        self.add_window = EditTemplateWindow(self.firebase, self.user_data, menu_ref=self,
                                              doc_id=t['id'], nome=t['nome'],
                                              texto=t['texto'], atalho=t.get('atalho',''))
        self.add_window.show(); self.add_window.raise_(); self.add_window.activateWindow()

    def delete_atalho(self, atl):
        show_confirm(self, 'Excluir este atalho?', lambda: self._do_delete_atalho(atl))

    def _do_delete_atalho(self, atl):
        self.firebase.delete_atalho(atl['id'])
        self._notification = NotificationWidget('✓ Atalho excluído!')
        self._notification.show()
        QTimer.singleShot(100, self.show_atalhos_tab)

    def show_edit_atalho_overlay(self, atl):
        self.show_add_atalho_overlay(atl_existente=atl)

    def delete_template(self, t):
        show_confirm(self, 'Excluir este template?', lambda: self._do_delete_template(t))

    def _do_delete_template(self, t):
        self.firebase.delete_template(t['id'])
        self._notification = NotificationWidget('✓ Template excluído!')
        self._notification.show()
        QTimer.singleShot(100, self.show_templates_tab)

    def editar_nome(self):
        overlay = OverlayDialog(self)
        overlay.content_card.setFixedSize(320, 170)

        title = QLabel("Editar nome")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:600; color:black; background:transparent; border:none; padding-bottom:10px;")
        overlay.add_content(title)

        inp = QLineEdit()
        inp.setText(self.user_data.get('nome', ''))
        inp.setStyleSheet("QLineEdit{padding:8px;border:2px solid #ddd;border-radius:8px;font-size:13px;background:white;color:black;}QLineEdit:focus{border:2px solid #B97E88;}")
        overlay.add_content(inp)

        overlay.card_layout.addStretch()

        btns = QHBoxLayout(); btns.setSpacing(10)
        b_cancelar = QPushButton("Cancelar")
        b_cancelar.setFixedHeight(34)
        b_cancelar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:#2d2d2d;background:transparent;border:2px solid #ccc;border-radius:8px;}QPushButton:hover{background:rgba(0,0,0,0.05);}")
        b_cancelar.clicked.connect(overlay.close)
        b_salvar = QPushButton("Salvar")
        b_salvar.setFixedHeight(34)
        b_salvar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:white;background:#82414C;border:none;border-radius:8px;}QPushButton:hover{background:#6d3640;}")
        def _salvar_nome():
            novo = inp.text().strip()
            if not novo:
                return
            self.firebase._upsert_usuario(self.user_data['uid'], novo, self.user_data['setor'],
                                           self.user_data['email'], aprovado=True,
                                           is_admin=self.user_data.get('is_admin', False))
            self.user_data['nome'] = novo
            overlay.close()
            self._notification = NotificationWidget('✓ Nome atualizado!')
            self._notification.show()
            self.show_config_tab()
        b_salvar.clicked.connect(_salvar_nome)
        btns.addWidget(b_cancelar); btns.addWidget(b_salvar)
        bw = QWidget(); bw.setStyleSheet("background:transparent;border:none;"); bw.setLayout(btns)
        overlay.add_content(bw)
        overlay.show()
        inp.setFocus()

    def editar_setor(self):
        overlay = OverlayDialog(self)
        overlay.content_card.setFixedSize(320, 60 + len(SETORES) * 38 + 60)

        title = QLabel("Editar setor")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:600; color:black; background:transparent; border:none; padding-bottom:6px;")
        overlay.add_content(title)

        atual = self.user_data.get('setor', '')
        selecionado = [atual]

        botoes = []
        for s in SETORES:
            btn = QPushButton(s)
            ativo = s == atual
            btn.setCheckable(True)
            btn.setChecked(ativo)
            btn.setFixedHeight(32)
            estilo_ativo   = "QPushButton{font-family:'Inter';font-size:12px;color:white;background:#82414C;border:none;border-radius:8px;}"
            estilo_inativo = "QPushButton{font-family:'Inter';font-size:12px;color:#2d2d2d;background:#f0f0f0;border:none;border-radius:8px;}QPushButton:hover{background:#e0e0e0;}"
            btn.setStyleSheet(estilo_ativo if ativo else estilo_inativo)
            def _on_click(checked, nome=s, b=btn):
                selecionado[0] = nome
                for ob in botoes:
                    ob.setStyleSheet(estilo_ativo if ob.text() == nome else estilo_inativo)
            btn.clicked.connect(_on_click)
            botoes.append(btn)
            overlay.add_content(btn)

        overlay.card_layout.addStretch()

        btns = QHBoxLayout(); btns.setSpacing(10)
        b_cancelar = QPushButton("Cancelar")
        b_cancelar.setFixedHeight(34)
        b_cancelar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:#2d2d2d;background:transparent;border:2px solid #ccc;border-radius:8px;}QPushButton:hover{background:rgba(0,0,0,0.05);}")
        b_cancelar.clicked.connect(overlay.close)
        b_salvar = QPushButton("Salvar")
        b_salvar.setFixedHeight(34)
        b_salvar.setStyleSheet("QPushButton{font-family:'Inter';font-size:13px;color:white;background:#82414C;border:none;border-radius:8px;}QPushButton:hover{background:#6d3640;}")
        def _salvar_setor():
            novo = selecionado[0]
            self.firebase._upsert_usuario(self.user_data['uid'], self.user_data['nome'], novo,
                                           self.user_data['email'], aprovado=True,
                                           is_admin=self.user_data.get('is_admin', False))
            self.user_data['setor'] = novo
            overlay.close()
            self._notification = NotificationWidget('✓ Setor atualizado!')
            self._notification.show()
            self.show_config_tab()
        b_salvar.clicked.connect(_salvar_setor)
        btns.addWidget(b_cancelar); btns.addWidget(b_salvar)
        bw = QWidget(); bw.setStyleSheet("background:transparent;border:none;"); bw.setLayout(btns)
        overlay.add_content(bw)
        overlay.show()

    def show_config_tab(self):
        if MainMenu._last_tab != 'config':
            self._tab_antes_config = MainMenu._last_tab
        MainMenu._last_tab = 'config'

        # limpar layout inteiro e reconstruir
        while self.config_content_layout.count():
            item = self.config_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        svg_voltar = """<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
<g clip-path="url(#clip0_43_90)">
<path d="M9.99996 6.66666L6.66663 10M6.66663 10L9.99996 13.3333M6.66663 10H13.3333M18.3333 10C18.3333 14.6024 14.6023 18.3333 9.99996 18.3333C5.39759 18.3333 1.66663 14.6024 1.66663 10C1.66663 5.39762 5.39759 1.66666 9.99996 1.66666C14.6023 1.66666 18.3333 5.39762 18.3333 10Z" stroke="#1E1E1E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</g><defs><clipPath id="clip0_43_90"><rect width="20" height="20" fill="white"/></clipPath></defs></svg>"""
        btn_voltar = QPushButton()
        btn_voltar.setIcon(create_svg_icon(svg_voltar, 20))
        btn_voltar.setIconSize(QSize(20, 20))
        btn_voltar.setFixedSize(32, 32)
        btn_voltar.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:6px;}QPushButton:hover{background:rgba(0,0,0,0.06);}")
        btn_voltar.clicked.connect(self.voltar_menu)
        self.config_content_layout.addWidget(btn_voltar, alignment=Qt.AlignmentFlag.AlignLeft)

        svg_edit_cfg = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M3.33333 12.6667H4.28333L10.8 6.15L9.85 5.2L3.33333 11.7167V12.6667ZM2 14V11.1667L10.8 2.38333C10.9333 2.26111 11.0806 2.16667 11.2417 2.1C11.4028 2.03333 11.5722 2 11.75 2C11.9278 2 12.1 2.03333 12.2667 2.1C12.4333 2.16667 12.5778 2.26667 12.7 2.4L13.6167 3.33333C13.75 3.45556 13.8472 3.6 13.9083 3.76667C13.9694 3.93333 14 4.1 14 4.26667C14 4.44444 13.9694 4.61389 13.9083 4.775C13.8472 4.93611 13.75 5.08333 13.6167 5.21667L4.83333 14H2ZM10.3167 5.68333L9.85 5.2L10.8 6.15L10.3167 5.68333Z" fill="#1D1B20"/></svg>"""

        def config_row(label_text, value_text, on_edit=None):
            row = QHBoxLayout(); row.setContentsMargins(0, 2, 0, 2)
            lbl = QLabel(); lbl.setText(f"{label_text}: <b>{value_text}</b>")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("font-family:'Inter'; font-size:12px; color:black; background:transparent; border:none;")
            row.addWidget(lbl); row.addStretch()
            btn = QPushButton()
            btn.setIcon(create_svg_icon(svg_edit_cfg, 16))
            btn.setIconSize(QSize(16, 16)); btn.setFixedSize(28, 28)
            btn.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;}QPushButton:hover{background:rgba(0,0,0,0.06);}")
            if on_edit: btn.clicked.connect(on_edit)
            row.addWidget(btn)
            w = QWidget(); w.setStyleSheet("background:transparent;"); w.setLayout(row)
            return w

        lbl_usuario = QLabel("Usuário")
        lbl_usuario.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:600; color:black; background:transparent; border:none;")
        self.config_content_layout.addWidget(lbl_usuario)

        linha = QFrame(); linha.setFrameShape(QFrame.Shape.HLine)
        linha.setStyleSheet("color:#C0C0C0; background:#C0C0C0; border:none; max-height:1px;")
        self.config_content_layout.addWidget(linha)

        self.config_content_layout.addWidget(config_row("Nome",  self.user_data.get('nome', '—'),  self.editar_nome))
        self.config_content_layout.addWidget(config_row("Setor", self.user_data.get('setor', '—'), self.editar_setor))
        self.config_content_layout.addWidget(config_row("Email", self.user_data.get('email', '—'), None))
        self.config_content_layout.addWidget(config_row("Senha", "*******", None))

        btn_logout = QPushButton("Log out")
        btn_logout.setStyleSheet("QPushButton{font-family:'Inter';font-size:11px;color:#900B09;background:transparent;border:none;text-decoration:underline;padding:0;}QPushButton:hover{color:#6a0807;}")
        btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_logout.setFixedHeight(20)
        btn_logout.clicked.connect(self.do_logout)
        self.config_content_layout.addWidget(btn_logout, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Seção: Animações ──────────────────────────────────────────────────
        linha_a = QFrame(); linha_a.setFrameShape(QFrame.Shape.HLine)
        linha_a.setStyleSheet("color:#C0C0C0; background:#C0C0C0; border:none; max-height:1px;")
        self.config_content_layout.addWidget(linha_a)

        anim_row = QHBoxLayout(); anim_row.setContentsMargins(0, 4, 0, 4)
        lbl_anim = QLabel("Animações")
        lbl_anim.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:600; color:black; background:transparent; border:none;")
        anim_row.addWidget(lbl_anim); anim_row.addStretch()

        anim_on = self.firebase.get_config('animacoes', True)
        if isinstance(anim_on, str): anim_on = anim_on.lower() != 'false'

        class ToggleSwitch(QWidget):
            def __init__(self, checked=True):
                super().__init__()
                self._checked = checked
                self.setFixedSize(44, 24)
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            def isChecked(self): return self._checked
            def setChecked(self, v): self._checked = v; self.update()
            def mousePressEvent(self, e):
                self._checked = not self._checked
                self.update()
                _on_toggle(self._checked)
            def paintEvent(self, e):
                from PyQt6.QtGui import QPainter, QColor, QPainterPath
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                bg = QColor("#88C22B") if self._checked else QColor("#C0C0C0")
                p.setBrush(bg); p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, 44, 24, 12, 12)
                p.setBrush(QColor("white")); p.setPen(Qt.PenStyle.NoPen)
                cx = 24 if self._checked else 4
                p.drawEllipse(cx, 3, 18, 18)

        toggle = ToggleSwitch(anim_on)

        def _on_toggle(checked):
            self.firebase.set_config('animacoes', checked)

        anim_row.addWidget(toggle)

        anim_w = QWidget(); anim_w.setStyleSheet("background:transparent;"); anim_w.setLayout(anim_row)
        self.config_content_layout.addWidget(anim_w)

        # ── Seção: Administração de usuários (só para admins) ─────────────────
        if self.user_data.get('is_admin', False):
            linha_adm = QFrame(); linha_adm.setFrameShape(QFrame.Shape.HLine)
            linha_adm.setStyleSheet("color:#C0C0C0; background:#C0C0C0; border:none; max-height:1px;")
            self.config_content_layout.addWidget(linha_adm)

            lbl_adm = QLabel("Administração de usuários")
            lbl_adm.setStyleSheet("font-family:'Inter'; font-size:14px; font-weight:600; color:black; background:transparent; border:none;")
            self.config_content_layout.addWidget(lbl_adm)

            # subtítulo "Ver usuários" com seta que gira
            svg_chevron = """<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2.5 5L7 9.5L11.5 5" stroke="#1E1E1E" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

            self._chevron_rotated = False
            self._usuarios_container = QWidget()
            self._usuarios_container.setVisible(False)
            self._usuarios_container.setStyleSheet("background:transparent;")
            uc_layout = QVBoxLayout(self._usuarios_container)
            uc_layout.setContentsMargins(8, 0, 8, 4)
            uc_layout.setSpacing(2)

            svg_del_u = """<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2 3.99998H3.33333M3.33333 3.99998H14M3.33333 3.99998L3.33333 13.3333C3.33333 13.6869 3.47381 14.0261 3.72386 14.2761C3.97391 14.5262 4.31304 14.6666 4.66667 14.6666H11.3333C11.687 14.6666 12.0261 14.5262 12.2761 14.2761C12.5262 14.0261 12.6667 13.6869 12.6667 13.3333V3.99998M5.33333 3.99998V2.66665C5.33333 2.31302 5.47381 1.97389 5.72386 1.72384C5.97391 1.47379 6.31304 1.33331 6.66667 1.33331H9.33333C9.68696 1.33331 10.0261 1.47379 10.2761 1.72384C10.5262 1.97389 10.6667 2.31302 10.6667 2.66665V3.99998" stroke="#900B09" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

            _uc_layout_ref     = uc_layout
            _uc_container_ref  = self._usuarios_container
            self._svg_del_u    = svg_del_u
            self._uc_layout_ref    = _uc_layout_ref
            self._uc_container_ref = _uc_container_ref

            def _popular_usuarios():
                try:
                    todos = self.firebase.get_approved_users()
                except Exception as e:
                    todos = []
                self._usuarios_loaded.emit(todos)

            import threading as _threading
            _threading.Thread(target=_popular_usuarios, daemon=True).start()

            self._btn_chevron = QPushButton()
            self._btn_chevron.setIcon(create_svg_icon(svg_chevron, 14))
            self._btn_chevron.setIconSize(QSize(14, 14))
            self._btn_chevron.setFixedSize(22, 22)
            self._btn_chevron.setStyleSheet("QPushButton{background:transparent;border:none;}")

            def _toggle_usuarios():
                self._chevron_rotated = not self._chevron_rotated
                from PyQt6.QtGui import QTransform, QIcon
                px = create_svg_icon(svg_chevron, 14).pixmap(14, 14)
                t = QTransform().rotate(180 if self._chevron_rotated else 0)
                self._btn_chevron.setIcon(QIcon(px.transformed(t)))
                self._usuarios_container.setVisible(self._chevron_rotated)

            self._btn_chevron.clicked.connect(_toggle_usuarios)

            ver_w = QWidget()
            ver_w.setStyleSheet("QWidget{background:transparent;} QWidget:hover{background:rgba(0,0,0,0.04); border-radius:6px;}")
            ver_w.setCursor(Qt.CursorShape.PointingHandCursor)
            ver_w.mousePressEvent = lambda e: _toggle_usuarios()
            ver_row = QHBoxLayout(ver_w)
            ver_row.setContentsMargins(0, 4, 0, 4); ver_row.setSpacing(6)
            lbl_ver = QLabel("Ver usuários")
            lbl_ver.setStyleSheet("font-family:'Inter'; font-size:12px; color:black; background:transparent; border:none;")
            ver_row.addWidget(lbl_ver)
            ver_row.addWidget(self._btn_chevron)
            ver_row.addStretch()

            self.config_content_layout.addWidget(ver_w)
            self.config_content_layout.addWidget(self._usuarios_container)

            # ── Usuários pendentes ────────────────────────────────────────────
            import threading
            pendentes_ref = [0]

            pend_w = QWidget()
            pend_w.setStyleSheet("QWidget{background:transparent;}")
            pend_row = QHBoxLayout(pend_w)
            pend_row.setContentsMargins(0, 4, 0, 4); pend_row.setSpacing(6)

            lbl_pend = QLabel("Usuários pendentes")
            lbl_pend.setStyleSheet("font-family:'Inter'; font-size:12px; color:black; background:transparent; border:none;")
            pend_row.addWidget(lbl_pend)

            bolinha = QLabel()
            bolinha.setFixedSize(10, 10)
            bolinha.setStyleSheet("background:#FF7C7C; border-radius:5px; border:none;")
            bolinha.setVisible(False)
            pend_row.addWidget(bolinha)
            pend_row.addStretch()

            # tooltip customizado (Tool window suprime tooltips nativos)
            self._pend_tooltip = QLabel("Nenhum usuário pendente de aprovação", self)
            self._pend_tooltip.setStyleSheet("""
                background:#333; color:white; border-radius:4px;
                padding:4px 8px; font-family:'Inter'; font-size:11px; border:none;
            """)
            self._pend_tooltip.setVisible(False)
            self._pend_tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

            def _do_show_tooltip():
                if pendentes_ref[0] != 0:
                    return
                pos = pend_w.mapTo(self, pend_w.rect().bottomLeft())
                self._pend_tooltip.move(pos.x(), pos.y() + 2)
                self._pend_tooltip.adjustSize()
                self._pend_tooltip.raise_()
                self._pend_tooltip.setVisible(True)

            def _show_tooltip():
                if pendentes_ref[0] == 0:
                    self._tooltip_timer = QTimer()
                    self._tooltip_timer.setSingleShot(True)
                    self._tooltip_timer.timeout.connect(_do_show_tooltip)
                    self._tooltip_timer.start(500)

            def _hide_tooltip():
                if hasattr(self, '_tooltip_timer'):
                    self._tooltip_timer.stop()
                self._pend_tooltip.setVisible(False)

            pend_w.enterEvent = lambda e: _show_tooltip()
            pend_w.leaveEvent = lambda e: _hide_tooltip()

            self._pend_container = QWidget()
            self._pend_container.setVisible(False)
            self._pend_container.setStyleSheet("background:transparent;")
            pc_layout = QVBoxLayout(self._pend_container)
            pc_layout.setContentsMargins(8, 4, 0, 4); pc_layout.setSpacing(4)

            def _toggle_pend():
                if pendentes_ref[0] == 0:
                    return
                vis = not self._pend_container.isVisible()
                self._pend_container.setVisible(vis)

            pend_w.setCursor(Qt.CursorShape.ArrowCursor)

            def _atualizar_pendentes(lista):
                pendentes_ref[0] = len(lista)
                bolinha.setVisible(len(lista) > 0)

                # limpar container
                while pc_layout.count():
                    item = pc_layout.takeAt(0)
                    if item.widget(): item.widget().deleteLater()

                # popular com os pendentes
                for u in lista:
                    _uid   = str(u.get('uid', ''))
                    _nome  = u.get('nome', '?')
                    _setor = u.get('setor', '')
                    _email = u.get('email', '')

                    row = QHBoxLayout(); row.setContentsMargins(16, 2, 0, 2); row.setSpacing(6)
                    lbl_nome = QLabel(_nome)
                    lbl_nome.setStyleSheet("font-family:'Inter'; font-size:12px; color:black; background:transparent; border:none;")
                    row.addWidget(lbl_nome); row.addStretch()

                    btn_apr = QPushButton("Aprovar")
                    btn_apr.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#2d8a2d;background:transparent;border:none;text-decoration:underline;}QPushButton:hover{color:#1a5c1a;}")
                    btn_apr.setCursor(Qt.CursorShape.PointingHandCursor)

                    btn_rec = QPushButton("Recusar")
                    btn_rec.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#900B09;background:transparent;border:none;text-decoration:underline;}QPushButton:hover{color:#6a0807;}")
                    btn_rec.setCursor(Qt.CursorShape.PointingHandCursor)

                    def _aprovar(checked=False, uid=_uid, nome=_nome, setor=_setor, email=_email):
                        self.firebase.approve_user(uid, nome, setor, email)
                        self._notification = NotificationWidget(f'✓ {nome} aprovado!')
                        self._notification.show()
                        self.show_config_tab()

                    def _recusar(checked=False, uid=_uid, nome=_nome):
                        show_confirm(self, f'Recusar {nome}?', lambda uid=uid, nome=nome: _do_recusar(uid, nome))

                    def _do_recusar(uid, nome):
                        self.firebase.reject_user(uid)
                        self._notification = NotificationWidget(f'✗ {nome} recusado!')
                        self._notification.show()
                        self.show_config_tab()

                    btn_apr.clicked.connect(_aprovar)
                    btn_rec.clicked.connect(_recusar)
                    row.addWidget(btn_apr); row.addWidget(btn_rec)

                    rw = QWidget(); rw.setStyleSheet("background:transparent;"); rw.setLayout(row)
                    pc_layout.addWidget(rw)

                if len(lista) > 0:
                    pend_w.setCursor(Qt.CursorShape.PointingHandCursor)
                    pend_w.setToolTip("")
                    pend_w.mousePressEvent = lambda e: _toggle_pend()
                else:
                    pend_w.setCursor(Qt.CursorShape.ArrowCursor)
                    pend_w.setToolTip("Nenhum usuário pendente de aprovação")
                    pend_w.mousePressEvent = lambda e: None

            # buscar pendentes em thread para não bloquear UI
            self.config_content_layout.addWidget(pend_w)
            self.config_content_layout.addWidget(self._pend_container)

            def _buscar_pendentes():
                try:
                    lista = self.firebase.get_pending_users()
                except:
                    lista = []
                QTimer.singleShot(0, lambda: _atualizar_pendentes(lista))

            import threading as _threading
            _threading.Thread(target=_buscar_pendentes, daemon=True).start()

        self.config_content_layout.addStretch()
        self.stack.setCurrentIndex(1)

    def _on_usuarios_loaded(self, todos):
        lay = getattr(self, '_uc_layout_ref', None)
        container = getattr(self, '_uc_container_ref', None)
        if lay is None or container is None:
            return

        while lay.count():
            item = lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        meu_uid = self.user_data.get('uid', '')
        outros = [u for u in todos if str(u.get('uid','')) != meu_uid]

        if not outros:
            vazio = QLabel("Nenhum outro usuário cadastrado.")
            vazio.setStyleSheet("font-family:'Inter'; font-size:11px; color:#888; background:transparent; border:none; padding-left:8px;")
            lay.addWidget(vazio)
        else:
            for u in outros:
                _uid      = str(u.get('uid', ''))
                _nome     = u.get('nome', '?')
                _setor    = u.get('setor', '')
                _is_admin = u.get('is_admin', False)
                row = QHBoxLayout(); row.setContentsMargins(0, 5, 0, 5); row.setSpacing(6)
                lbl = QLabel(f"{_nome} - {_setor}")
                lbl.setStyleSheet("font-family:'Inter'; font-size:12px; color:black; background:transparent; border:none;")
                row.addWidget(lbl); row.addStretch()
                btn_admin = QPushButton("Tornar admin")
                btn_admin.setStyleSheet("QPushButton{font-family:'Inter';font-size:12px;color:#1D1B20;background:transparent;border:none;text-decoration:underline;}QPushButton:hover{color:#444;}")
                btn_admin.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_admin.setVisible(not _is_admin)
                btn_del_u = QPushButton()
                btn_del_u.setIcon(create_svg_icon(getattr(self, '_svg_del_u', ''), 16))
                btn_del_u.setIconSize(QSize(16, 16))
                btn_del_u.setFixedSize(25, 25)
                btn_del_u.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:4px;padding:0;}QPushButton:hover{background:rgba(144,11,9,0.08);}")
                def _tornar_admin(checked=False, uid=_uid, nome=_nome):
                    show_confirm(self, f'Tornar {nome} administrador?', lambda uid=uid, nome=nome: self._do_admin(uid, nome))
                def _excluir_user(checked=False, uid=_uid, nome=_nome):
                    show_confirm(self, f'Excluir {nome}?', lambda uid=uid, nome=nome: self._do_excluir_user(uid, nome))
                btn_admin.clicked.connect(_tornar_admin)
                btn_del_u.clicked.connect(_excluir_user)
                row.addWidget(btn_admin); row.addWidget(btn_del_u)
                rw = QWidget(); rw.setStyleSheet("background:transparent;"); rw.setLayout(row)
                lay.addWidget(rw)

        container.adjustSize()
        container.updateGeometry()
        if container.isVisible():
            container.hide(); container.show()
        # forçar scroll a recalcular
        if hasattr(self, 'scroll_area'):
            self.scroll_area.widget().adjustSize()

    def _do_admin(self, uid, nome):
        self.firebase.promote_to_admin(uid)
        self._notification = NotificationWidget(f'✓ {nome} é admin agora!')
        self._notification.show()
        self.show_config_tab()

    def _do_excluir_user(self, uid, nome):
        self.firebase.delete_user(uid)
        self._notification = NotificationWidget(f'✗ {nome} excluído!')
        self._notification.show()
        self.show_config_tab()

    def do_logout(self):
        self.firebase.logout()
        self.close()
        if self.circle_parent:
            self.circle_parent.close()
        from PyQt6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            w.close()
        # reinicia a janela de login
        login = LoginWindow(self.firebase)
        login.login_success.connect(lambda user_data: self._on_relogin(user_data, login))
        login.show()
        self._login_ref = login

    def _on_relogin(self, user_data, login):
        login.close()
        new_circle = FloatingCircle(self.firebase, user_data)
        new_circle.show()
        new_circle.raise_()

    def voltar_menu(self):
        self.stack.setCurrentIndex(0)
        MainMenu._last_tab = getattr(self, '_tab_antes_config', 'templates')
        self._reload_current_tab()

    def confirmar_sair(self):
        show_confirm(self, 'Deseja fechar o programa?', QApplication.quit)

    def toggle_search(self):
        self._search_open = not self._search_open
        self.search_container.setVisible(self._search_open)
        self.btn_search.setVisible(not self._search_open)
        if self._search_open:
            self.search_input.setFocus()
            self.search_input.clear()
        else:
            self.search_input.clear()
            self._reload_current_tab()

    def on_search_changed(self, text):
        if not self._search_open:
            return
        tab = MainMenu._last_tab
        sub = MainMenu._last_sub_tab_templates if tab == 'templates' else MainMenu._last_sub_tab_atalhos
        apenas_meus = (sub == 'meus')
        uid   = self.user_data['uid']
        setor = self.user_data['setor']

        if not text.strip():
            self._reload_current_tab()
            return

        q = text.lower()
        if tab == 'templates':
            source = (self.firebase.get_templates_meus(uid) if apenas_meus
                      else self.firebase.get_templates_setor(setor))
            filtered = [t for t in source if q in t['nome'].lower() or q in t['texto'].lower()]
            self._on_templates_loaded(filtered, apenas_meus)

    def _reload_current_tab(self):
        if MainMenu._last_tab == 'templates':
            sub = MainMenu._last_sub_tab_templates
            self._load_templates(apenas_meus=(sub == 'meus'))
        else:
            sub = MainMenu._last_sub_tab_atalhos
            self._load_atalhos(apenas_meus=(sub == 'meus'))

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
        self.setStyleSheet("QWidget { background-color:#DEDDD2; }")
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