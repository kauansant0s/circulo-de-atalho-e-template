import sys
import sqlite3
import time
import json
import requests
import hashlib
from firebase_config import FIREBASE_CONFIG, SETORES
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QTextEdit, QMessageBox, QScrollArea, QListWidget, 
                              QListWidgetItem, QSpinBox, QComboBox, QCheckBox, QGroupBox, QTabWidget)
from PyQt6.QtCore import Qt, QPoint, QTimer, QObject, pyqtSignal, QRect, QMimeData, QPropertyAnimation, QEasingCurve, QSize, pyqtProperty
from PyQt6.QtGui import QCursor, QPainter, QColor, QDrag, QPen, QRadialGradient
from pynput import keyboard, mouse
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController


class NotificationWidget(QWidget):
    def __init__(self, message):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Container
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
        
        # Posicionar no canto inferior direito
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 50)
        
        # Auto-fechar após 2 segundos
        QTimer.singleShot(2000, self.close)
        
        # Fade out
        self.setWindowOpacity(1.0)
        self.fade_timer = QTimer()
        self.fade_timer.timeout.connect(self.fade_out)
        QTimer.singleShot(1500, self.fade_timer.start)
        self.opacity = 1.0
    
    def fade_out(self):
        self.opacity -= 0.1
        if self.opacity <= 0:
            self.fade_timer.stop()
            self.close()
        else:
            self.setWindowOpacity(self.opacity)


class Database:
    def __init__(self, user_id=None, user_setor=None):
        self.conn = sqlite3.connect('assistivetouch.db', check_same_thread=False)
        self.user_id = user_id
        self.user_setor = user_setor
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='templates'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            cursor.execute("PRAGMA table_info(templates)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'atalho' not in columns:
                cursor.execute('ALTER TABLE templates ADD COLUMN atalho TEXT')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    texto TEXT NOT NULL,
                    atalho TEXT,
                    usuario_id TEXT,
                    setor TEXT
                )
            ''')

        # Adicionar colunas de multi-usuário se não existirem
        cursor.execute("PRAGMA table_info(templates)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'usuario_id' not in columns:
            cursor.execute('ALTER TABLE templates ADD COLUMN usuario_id TEXT')
        if 'setor' not in columns:
            cursor.execute('ALTER TABLE templates ADD COLUMN setor TEXT')
        
        # Tabela de atalhos (shortcuts de automação)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shortcuts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                ativo INTEGER DEFAULT 1,
                acoes TEXT NOT NULL,
                tecla_atalho TEXT,
                usuario_id TEXT,
                setor TEXT
            )
        ''')

        # Adicionar colunas de multi-usuário em shortcuts
        cursor.execute("PRAGMA table_info(shortcuts)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'usuario_id' not in columns:
            cursor.execute('ALTER TABLE shortcuts ADD COLUMN usuario_id TEXT')
        if 'setor' not in columns:
            cursor.execute('ALTER TABLE shortcuts ADD COLUMN setor TEXT')

        # Verificar se coluna tecla_atalho existe
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shortcuts'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(shortcuts)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'tecla_atalho' not in columns:
                cursor.execute('ALTER TABLE shortcuts ADD COLUMN tecla_atalho TEXT')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
        ''')
        
        self.conn.commit()
    
    def get_config(self, chave, default=None):
        cursor = self.conn.cursor()
        cursor.execute('SELECT valor FROM config WHERE chave = ?', (chave,))
        row = cursor.fetchone()
        return row[0] if row else default
    
    def set_config(self, chave, valor):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)', (chave, str(valor)))
        self.conn.commit()
    
    def add_template(self, nome, texto, atalho=None):
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO templates (nome, texto, atalho, usuario_id, setor) VALUES (?, ?, ?, ?, ?)', 
                    (nome, texto, atalho, self.user_id, self.user_setor))
        self.conn.commit()
    
    def get_templates(self, apenas_meus=False):
        cursor = self.conn.cursor()
        if apenas_meus:
            # Apenas templates do próprio usuário
            cursor.execute('SELECT id, nome, texto, atalho FROM templates WHERE usuario_id = ?', (self.user_id,))
        else:
            # Templates do setor (incluindo os meus)
            cursor.execute('SELECT id, nome, texto, atalho FROM templates WHERE setor = ?', (self.user_setor,))
        return cursor.fetchall()
    
    def search_templates(self, query, apenas_meus=False):
        cursor = self.conn.cursor()
        if apenas_meus:
            cursor.execute('''
                SELECT id, nome, texto, atalho FROM templates 
                WHERE (nome LIKE ? OR texto LIKE ?) AND usuario_id = ?
            ''', (f'%{query}%', f'%{query}%', self.user_id))
        else:
            cursor.execute('''
                SELECT id, nome, texto, atalho FROM templates 
                WHERE (nome LIKE ? OR texto LIKE ?) AND setor = ?
            ''', (f'%{query}%', f'%{query}%', self.user_setor))
        return cursor.fetchall()
    
    def delete_template(self, id):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM templates WHERE id = ?', (id,))
        self.conn.commit()
    
    def update_template(self, id, nome, texto, atalho=None):
        """Atualizar template existente"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE templates SET nome = ?, texto = ?, atalho = ? WHERE id = ?', 
                      (nome, texto, atalho, id))
        self.conn.commit()
    
    # Métodos para Shortcuts
    def add_shortcut(self, nome, acoes, tecla_atalho=None):
        cursor = self.conn.cursor()
        acoes_json = json.dumps(acoes)
        cursor.execute('INSERT INTO shortcuts (nome, ativo, acoes, tecla_atalho, usuario_id, setor) VALUES (?, 1, ?, ?, ?, ?)', 
                    (nome, acoes_json, tecla_atalho, self.user_id, self.user_setor))
        self.conn.commit()
    
    def update_shortcut(self, id, nome, acoes, tecla_atalho=None):
        cursor = self.conn.cursor()
        acoes_json = json.dumps(acoes)
        cursor.execute('UPDATE shortcuts SET nome = ?, acoes = ?, tecla_atalho = ?, usuario_id = ?, setor = ? WHERE id = ?',
                    (nome, acoes_json, tecla_atalho, self.user_id, self.user_setor, id))
        self.conn.commit()
    
    def get_shortcuts(self, apenas_meus=False):
        cursor = self.conn.cursor()
        if apenas_meus:
            cursor.execute('SELECT id, nome, ativo, acoes, tecla_atalho FROM shortcuts WHERE usuario_id = ?', (self.user_id,))
        else:
            cursor.execute('SELECT id, nome, ativo, acoes, tecla_atalho FROM shortcuts WHERE setor = ?', (self.user_setor,))
        
        results = cursor.fetchall()
        shortcuts = []
        for row in results:
            shortcuts.append({
                'id': row[0],
                'nome': row[1],
                'ativo': row[2] == 1,
                'acoes': json.loads(row[3]),
                'tecla_atalho': row[4]
            })
        return shortcuts
    
    def toggle_shortcut(self, id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT ativo, tecla_atalho FROM shortcuts WHERE id = ?', (id,))
        result = cursor.fetchone()
        if result:
            novo_status = 0 if result[0] == 1 else 1
            cursor.execute('UPDATE shortcuts SET ativo = ? WHERE id = ?', (novo_status, id))
            
            # Se ativando E tem tecla, desativar outros com mesma tecla
            if novo_status == 1 and result[1]:
                cursor.execute(
                    'UPDATE shortcuts SET ativo = 0 WHERE tecla_atalho = ? AND id != ?',
                    (result[1], id)
                )
            self.conn.commit()
    
    def get_conflito_tecla(self, tecla, excluir_id=None):
        """Retorna atalhos com a mesma tecla (exceto o próprio ao editar)"""
        cursor = self.conn.cursor()
        if excluir_id:
            cursor.execute(
                'SELECT id, nome, ativo FROM shortcuts WHERE tecla_atalho = ? AND id != ?',
                (tecla, excluir_id)
            )
        else:
            cursor.execute(
                'SELECT id, nome, ativo FROM shortcuts WHERE tecla_atalho = ?',
                (tecla,)
            )
        return cursor.fetchall()  # [(id, nome, ativo), ...]
    
    def desativar_conflitos_tecla(self, tecla, excluir_id=None):
        """Desativa todos os atalhos com a mesma tecla, exceto o especificado"""
        cursor = self.conn.cursor()
        if excluir_id:
            cursor.execute(
                'UPDATE shortcuts SET ativo = 0 WHERE tecla_atalho = ? AND id != ?',
                (tecla, excluir_id)
            )
        else:
            cursor.execute(
                'UPDATE shortcuts SET ativo = 0 WHERE tecla_atalho = ?',
                (tecla,)
            )
        self.conn.commit()
    
    def delete_shortcut(self, id):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM shortcuts WHERE id = ?', (id,))
        self.conn.commit()
    
    def save_position(self, x, y):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)', 
                      ('circle_x', str(x)))
        cursor.execute('INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)', 
                      ('circle_y', str(y)))
        self.conn.commit()
    
    def get_position(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT valor FROM config WHERE chave = ?', ('circle_x',))
        x_result = cursor.fetchone()
        cursor.execute('SELECT valor FROM config WHERE chave = ?', ('circle_y',))
        y_result = cursor.fetchone()
        
        if x_result and y_result:
            return int(x_result[0]), int(y_result[0])
        return None

class FirebaseAuth:
    """Gerenciador de autenticação Firebase"""
    
    def __init__(self):
        self.api_key = FIREBASE_CONFIG['apiKey']
        self.project_id = FIREBASE_CONFIG['projectId']
        self.current_user = None
        self.id_token = None
        
    def signup(self, email, password, nome, username, setor, email_real):
        """Criar conta (fica pendente de aprovação)"""
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}"
        data = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }
        
        response = requests.post(url, json=data)
        if response.status_code == 200:
            result = response.json()
            uid = result['localId']
            self.id_token = result['idToken']
            
            # Verificar se é a primeira conta (nenhum usuário aprovado existe)
            if self.is_first_user():
                # Primeira conta: aprovar automaticamente como admin
                self.approve_user_direct(uid, nome, username, setor, email, is_admin=True)
                return {'success': True, 'uid': uid, 'first_admin': True}
            else:
                # Demais contas: salvar como pendente
                self.save_pending_user(uid, nome, username, setor, email)
                return {'success': True, 'uid': uid}
        else:
            error = response.json().get('error', {}).get('message', 'Erro desconhecido')
            return {'success': False, 'error': error}
    
    def login(self, email, password):
        """Login com email/senha"""
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={self.api_key}"
        data = {
            "email": email,
            "password": password,
            "returnSecureToken": True
        }
        
        try:
            response = requests.post(url, json=data)
            print(f"DEBUG login response status: {response.status_code}")
            print(f"DEBUG login response: {response.text}")
            
            if response.status_code == 200:
                result = response.json()
                self.id_token = result['idToken']
                uid = result['localId']
                
                # Buscar dados do usuário
                user_data = self.get_user_data(uid)
                if user_data and user_data.get('aprovado'):
                    self.current_user = user_data
                    self.current_user['uid'] = uid
                    return {'success': True, 'user': self.current_user}
                else:
                    return {'success': False, 'error': 'Usuário aguardando aprovação'}
            else:
                error_data = response.json()
                error_msg = error_data.get('error', {}).get('message', 'Erro desconhecido')
                print(f"DEBUG erro: {error_msg}")
                return {'success': False, 'error': 'Email ou senha incorretos'}
        except Exception as e:
            print(f"DEBUG exception: {e}")
            return {'success': False, 'error': f'Erro de conexão: {str(e)}'}
    
    def save_pending_user(self, uid, nome, username, setor, email, email_real):
        """Salvar usuário pendente no Firestore"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/pending_users/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        data = {
            "fields": {
                "nome": {"stringValue": nome},
                "username": {"stringValue": username},
                "setor": {"stringValue": setor},
                "email": {"stringValue": email},
                "email_real": {"stringValue": email_real},
                "aprovado": {"booleanValue": False}
            }
        }
        requests.patch(url, headers=headers, json=data)
    
    def get_user_data(self, uid):
        """Buscar dados do usuário aprovado"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            fields = data.get('fields', {})
            return {
                'nome': fields.get('nome', {}).get('stringValue', ''),
                'username': fields.get('username', {}).get('stringValue', ''),
                'setor': fields.get('setor', {}).get('stringValue', ''),
                'aprovado': fields.get('aprovado', {}).get('booleanValue', False),
                'is_admin': fields.get('is_admin', {}).get('booleanValue', False)
            }
        return None
    
    def logout(self):
        """Deslogar"""
        self.current_user = None
        self.id_token = None

    def is_first_user(self):
        """Verificar se já existe algum usuário aprovado"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return len(data.get('documents', [])) == 0  # True se não tem ninguém
            return True  # Se der erro, assume que é o primeiro
        except:
            return True

    def approve_user_direct(self, uid, nome, username, setor, email, is_admin=False):
        """Aprovar usuário direto (para primeira conta)"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        data = {
            "fields": {
                "nome": {"stringValue": nome},
                "username": {"stringValue": username},
                "setor": {"stringValue": setor},
                "email": {"stringValue": email},
                "aprovado": {"booleanValue": True},
                "is_admin": {"booleanValue": is_admin}
            }
        }
        requests.patch(url, headers=headers, json=data)

    def get_pending_users(self):
        """Buscar usuários aguardando aprovação"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/pending_users"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            users = []
            for doc in data.get('documents', []):
                uid = doc['name'].split('/')[-1]
                fields = doc.get('fields', {})
                users.append({
                    'uid': uid,
                    'nome': fields.get('nome', {}).get('stringValue', ''),
                    'username': fields.get('username', {}).get('stringValue', ''),
                    'setor': fields.get('setor', {}).get('stringValue', ''),
                    'email': fields.get('email', {}).get('stringValue', '')
                })
            return users
        return []
    
    def approve_user(self, uid, nome, username, setor, email):
        """Aprovar usuário - move de pending para usuarios"""
        # 1. Criar documento em usuarios/
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        data = {
            "fields": {
                "nome": {"stringValue": nome},
                "username": {"stringValue": username},
                "setor": {"stringValue": setor},
                "email": {"stringValue": email},
                "aprovado": {"booleanValue": True},
                "is_admin": {"booleanValue": False}
            }
        }
        requests.patch(url, headers=headers, json=data)
        
        # 2. Deletar de pending_users/
        url_delete = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/pending_users/{uid}"
        requests.delete(url_delete, headers=headers)
        
        return True
    
    def reject_user(self, uid):
        """Rejeitar usuário - deleta de pending"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/pending_users/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        requests.delete(url, headers=headers)
        return True
    
    def get_approved_users(self):
        """Buscar usuários já aprovados"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            users = []
            for doc in data.get('documents', []):
                uid = doc['name'].split('/')[-1]
                fields = doc.get('fields', {})
                users.append({
                    'uid': uid,
                    'nome': fields.get('nome', {}).get('stringValue', ''),
                    'email': fields.get('email', {}).get('stringValue', ''),
                    'setor': fields.get('setor', {}).get('stringValue', ''),
                    'is_admin': fields.get('is_admin', {}).get('booleanValue', False)
                })
            return users
        return []

    def promote_to_admin(self, uid):
        """Promover usuário a administrador"""
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/usuarios/{uid}"
        headers = {"Authorization": f"Bearer {self.id_token}"}
        
        # Buscar dados atuais
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            doc = response.json()
            fields = doc.get('fields', {})
            
            # Atualizar is_admin para true
            fields['is_admin'] = {"booleanValue": True}
            
            # Salvar
            requests.patch(url, headers=headers, json={"fields": fields})
            return True
        return False

class LoginWindow(QWidget):
    """Tela de login/cadastro"""
    login_success = pyqtSignal(dict)
    
    def __init__(self, firebase_auth):
        super().__init__()
        self.firebase = firebase_auth
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle('AssistiveTouch - Login')
        self.setFixedWidth(400)
        self.setWindowFlags(Qt.WindowType.Window)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(40, 20, 40, 40)
        layout.setSpacing(10)
        
        # Logo/Título
        titulo = QLabel('🔘 AssistiveTouch')
        titulo.setStyleSheet('font-size: 24px; font-weight: bold; color: #2d2d2d;')
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(titulo)
        
        subtitulo = QLabel('Sistema de Automação Multi-usuário')
        subtitulo.setStyleSheet('font-size: 12px; color: #666;')
        subtitulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitulo)
        
        layout.addSpacing(10)
        
        # Tabs Login/Cadastro
        self.tabs = QTabWidget()
        
        # Tab Login
        login_tab = QWidget()
        login_layout = QVBoxLayout()
        
        login_layout.addWidget(QLabel('Email:'))
        self.login_email = QLineEdit()
        self.login_email.setPlaceholderText('seu@email.com')
        login_layout.addWidget(self.login_email)
        
        login_layout.addWidget(QLabel('Senha:'))
        self.login_senha = QLineEdit()
        self.login_senha.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_senha.setPlaceholderText('••••••••')
        login_layout.addWidget(self.login_senha)
        
        btn_login = QPushButton('Entrar')
        btn_login.setStyleSheet("""
            QPushButton {
                background-color: #406e54;
                color: white;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        btn_login.clicked.connect(self.do_login)
        login_layout.addWidget(btn_login)

        esqueci_senha = QLabel('<a href="#" style="color: #406e54;">Esqueci minha senha</a>')
        esqueci_senha.setAlignment(Qt.AlignmentFlag.AlignCenter)
        esqueci_senha.linkActivated.connect(self.esqueci_senha)
        login_layout.addWidget(esqueci_senha)
        
        login_tab.setLayout(login_layout)
        
        # Tab Cadastro
        cadastro_tab = QWidget()
        cadastro_layout = QVBoxLayout()
        
        cadastro_layout.addWidget(QLabel('Nome Completo:'))
        self.cad_nome = QLineEdit()
        cadastro_layout.addWidget(self.cad_nome)

        cadastro_layout.addWidget(QLabel('Email:'))
        self.cad_email_real = QLineEdit()
        self.cad_email_real.setPlaceholderText('seu@email.com')
        cadastro_layout.addWidget(self.cad_email_real)
        
        cadastro_layout.addWidget(QLabel('Senha:'))
        self.cad_senha = QLineEdit()
        self.cad_senha.setEchoMode(QLineEdit.EchoMode.Password)
        cadastro_layout.addWidget(self.cad_senha)

        cadastro_layout.addWidget(QLabel('Confirmar Senha:'))
        self.cad_senha_confirm = QLineEdit()
        self.cad_senha_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        cadastro_layout.addWidget(self.cad_senha_confirm)
        
        cadastro_layout.addWidget(QLabel('Setor:'))
        self.cad_setor = QComboBox()
        self.cad_setor.addItems(SETORES)
        cadastro_layout.addWidget(self.cad_setor)
        
        btn_cadastro = QPushButton('Criar Conta')
        btn_cadastro.setStyleSheet("""
            QPushButton {
                background-color: #88c22b;
                color: white;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #76a824;
            }
        """)
        btn_cadastro.clicked.connect(self.do_cadastro)
        cadastro_layout.addWidget(btn_cadastro)
        
        cadastro_tab.setLayout(cadastro_layout)
        
        self.tabs.addTab(login_tab, 'Login')
        self.tabs.addTab(cadastro_tab, 'Cadastro')

        self.tabs.currentChanged.connect(self.adjust_size)
        
        layout.addWidget(self.tabs)

        # Conectar mudança de aba para ajustar altura
        self.tabs.currentChanged.connect(lambda idx: self.setFixedHeight(380 if idx == 0 else 520))

        self.setLayout(layout)

        # Definir altura inicial (aba Login)
        self.setFixedHeight(350)
        
        self.setLayout(layout)
    
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
        nome = self.cad_nome.text().strip()
        email = self.cad_email_real.text().strip()
        senha = self.cad_senha.text()
        senha_confirm = self.cad_senha_confirm.text()
        setor = self.cad_setor.currentText()
        
        if not all([nome, email, senha, senha_confirm]):
            QMessageBox.warning(self, 'Erro', 'Preencha todos os campos!')
            return
        
        if senha != senha_confirm:
            QMessageBox.warning(self, 'Erro', 'As senhas não coincidem!')
            return
        
        # Gerar username a partir do email (parte antes do @)
        username = email.split('@')[0]
        
        result = self.firebase.signup(email, senha, nome, username, setor, email)
        if result['success']:
            if result.get('first_admin'):
                QMessageBox.information(
                    self, 
                    'Conta Admin Criada!',
                    'Parabéns! Você é o primeiro usuário e foi configurado como administrador.\n\n'
                    'Faça login para começar a usar o sistema.'
                )
            else:
                QMessageBox.information(
                    self, 
                    'Cadastro Enviado',
                    'Sua conta foi criada e está aguardando aprovação do administrador.\n\n'
                    'Você receberá acesso assim que for aprovado.'
                )
            self.tabs.setCurrentIndex(0)  # Voltar pra tab login
            # Limpar campos
            self.cad_nome.clear()
            self.cad_email_real.clear()
            self.cad_senha.clear()
            self.cad_senha_confirm.clear()
        else:
            QMessageBox.warning(self, 'Erro', result['error'])

    def adjust_size(self):
        """Ajustar tamanho da janela ao trocar de aba"""
        # Forçar recalcular tamanho
        self.adjustSize()
        self.updateGeometry()
        QApplication.processEvents()

    def esqueci_senha(self):
        """Enviar email de recuperação de senha"""
        email = self.login_email.text().strip()
        
        if not email:
            QMessageBox.warning(self, 'Erro', 'Digite seu email primeiro!')
            return
        
        # Chamar Firebase para enviar email de reset
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={self.firebase.api_key}"
        data = {
            "requestType": "PASSWORD_RESET",
            "email": email
        }
        
        try:
            response = requests.post(url, json=data)
            if response.status_code == 200:
                QMessageBox.information(
                    self, 
                    'Email Enviado',
                    'Um email de recuperação foi enviado para seu endereço.\n\nVerifique sua caixa de entrada.'
                )
            else:
                QMessageBox.warning(self, 'Erro', 'Email não encontrado!')
        except:
            QMessageBox.warning(self, 'Erro', 'Erro ao enviar email!')

class ManageUsersWindow(QWidget):
    """Janela para admin aprovar/rejeitar usuários"""
    
    def __init__(self, firebase):
        super().__init__()
        self.firebase = firebase
        self.init_ui()
    
    def init_ui(self):
        self.setWindowTitle('Gerenciar Usuários Pendentes')
        self.setFixedSize(500, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Título
        titulo = QLabel('👥 Usuários Aguardando Aprovação')
        titulo.setStyleSheet('font-size: 16px; font-weight: bold; color: #2d2d2d;')
        layout.addWidget(titulo)
        
        layout.addSpacing(10)
        
        # Scroll com lista de usuários
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #f5f5f5; }")
        
        self.users_container = QWidget()
        self.users_layout = QVBoxLayout()
        self.users_layout.setSpacing(10)
        
        self.load_pending_users()
        
        self.users_container.setLayout(self.users_layout)
        scroll.setWidget(self.users_container)
        layout.addWidget(scroll)
        
        # Botão fechar
        btn_fechar = QPushButton('Fechar')
        btn_fechar.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                padding: 10px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        btn_fechar.clicked.connect(self.close)
        layout.addWidget(btn_fechar)
        
        self.setLayout(layout)
    
    def load_pending_users(self):
        """Carregar lista de usuários pendentes E aprovados"""
        # Limpar lista atual
        while self.users_layout.count():
            child = self.users_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Buscar pendentes
        pending = self.firebase.get_pending_users()
        
        # Buscar aprovados (para promover a admin)
        approved = self.firebase.get_approved_users()
        
        if not pending and not approved:
            empty = QLabel('✓ Nenhum usuário para gerenciar')
            empty.setStyleSheet('color: #666; font-style: italic; padding: 40px; text-align: center;')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.users_layout.addWidget(empty)
        else:
            # Seção de pendentes
            if pending:
                titulo_pending = QLabel('👤 Aguardando Aprovação')
                titulo_pending.setStyleSheet('font-weight: bold; font-size: 13px; color: #2d2d2d; padding: 10px 0;')
                self.users_layout.addWidget(titulo_pending)
                
                for user in pending:
                    user_card = self.create_pending_card(user)
                    self.users_layout.addWidget(user_card)
            
            # Seção de aprovados (não-admins)
            if approved:
                titulo_approved = QLabel('✓ Usuários Aprovados')
                titulo_approved.setStyleSheet('font-weight: bold; font-size: 13px; color: #2d2d2d; padding: 10px 0; margin-top: 20px;')
                self.users_layout.addWidget(titulo_approved)
                
                for user in approved:
                    if not user.get('is_admin'):  # Só mostrar não-admins
                        user_card = self.create_approved_card(user)
                        self.users_layout.addWidget(user_card)
        
        self.users_layout.addStretch()
    
    def create_pending_card(self, user):
        """Criar card para cada usuário pendente"""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 8px;
                border: 2px solid #e0e0e0;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Nome e username
        nome_label = QLabel(f"👤 {user['nome']}")
        nome_label.setStyleSheet('font-size: 14px; font-weight: bold; color: #2d2d2d;')
        layout.addWidget(nome_label)
        
        username_label = QLabel(f"@{user['username']}")
        username_label.setStyleSheet('font-size: 12px; color: #666;')
        layout.addWidget(username_label)
        
        setor_label = QLabel(f"🏢 {user['setor']}")
        setor_label.setStyleSheet('font-size: 12px; color: #666;')
        layout.addWidget(setor_label)
        
        # Botões
        buttons_layout = QHBoxLayout()
        
        btn_aprovar = QPushButton('✓ Aprovar')
        btn_aprovar.setStyleSheet("""
            QPushButton {
                background-color: #88c22b;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #76a824;
            }
        """)
        btn_aprovar.clicked.connect(lambda: self.approve_user(user))
        buttons_layout.addWidget(btn_aprovar)
        
        btn_rejeitar = QPushButton('✗ Rejeitar')
        btn_rejeitar.setStyleSheet("""
            QPushButton {
                background-color: #82414c;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6d363f;
            }
        """)
        btn_rejeitar.clicked.connect(lambda: self.reject_user(user))
        buttons_layout.addWidget(btn_rejeitar)
        
        layout.addLayout(buttons_layout)
        card.setLayout(layout)
        return card
    
    def create_approved_card(self, user):
        """Criar card para usuário aprovado (opção de promover a admin)"""
        card = QWidget()
        card.setStyleSheet("""
            QWidget {
                background-color: #f0fff0;
                border-radius: 8px;
                border: 2px solid #88c22b;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        
        nome_label = QLabel(f"👤 {user['nome']}")
        nome_label.setStyleSheet('font-size: 14px; font-weight: bold; color: #2d2d2d;')
        layout.addWidget(nome_label)
        
        email_label = QLabel(f"✉️ {user['email']}")
        email_label.setStyleSheet('font-size: 12px; color: #666;')
        layout.addWidget(email_label)
        
        setor_label = QLabel(f"🏢 {user['setor']}")
        setor_label.setStyleSheet('font-size: 12px; color: #666;')
        layout.addWidget(setor_label)
        
        # Botão promover a admin
        btn_promover = QPushButton('⭐ Promover a Administrador')
        btn_promover.setStyleSheet("""
            QPushButton {
                background-color: #ffa500;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff8c00;
            }
        """)
        btn_promover.clicked.connect(lambda: self.promote_to_admin(user))
        layout.addWidget(btn_promover)
        
        card.setLayout(layout)
        return card
    
    def approve_user(self, user):
        """Aprovar usuário"""
        result = self.firebase.approve_user(
            user['uid'], 
            user['nome'], 
            user['username'], 
            user['setor'],
            user['email']
        )
        if result:
            QMessageBox.information(self, 'Sucesso', f'Usuário {user["nome"]} aprovado!')
            self.load_pending_users()  # Recarregar lista
    
    def reject_user(self, user):
        """Rejeitar usuário"""
        msg = QMessageBox()
        msg.setWindowTitle('Confirmar Rejeição')
        msg.setText(f'Deseja realmente rejeitar {user["nome"]}?')
        
        btn_sim = msg.addButton('Sim, rejeitar', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Cancelar', QMessageBox.ButtonRole.NoRole)
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.firebase.reject_user(user['uid'])
            QMessageBox.information(self, 'Rejeitado', f'Usuário {user["nome"]} foi rejeitado.')
            self.load_pending_users()  # Recarregar lista

    def promote_to_admin(self, user):
        """Promover usuário a admin"""
        msg = QMessageBox()
        msg.setWindowTitle('Confirmar Promoção')
        msg.setText(f'Promover {user["nome"]} a Administrador?\n\nAdministradores podem aprovar usuários e promover outros admins.')
        
        btn_sim = msg.addButton('Sim, promover', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Cancelar', QMessageBox.ButtonRole.NoRole)
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.firebase.promote_to_admin(user['uid'])
            QMessageBox.information(self, 'Promovido!', f'{user["nome"]} agora é administrador!')
            self.load_pending_users()  # Recarregar lista

class KeyboardSignals(QObject):
    show_popup = pyqtSignal(int, int)
    update_popup = pyqtSignal(str)
    close_popup = pyqtSignal()
    insert_text = pyqtSignal(str, int)

class KeyboardListener:
    def __init__(self, db):
        self.db = db
        self.typed_text = ""
        self.keyboard_controller = KeyboardController()
        self.mouse_controller = MouseController()
        self.listener = None
        self.templates_popup = None
        self.search_mode = False
        self.search_query = ""
        self.signals = KeyboardSignals()
        self.alt_pressed = False
        
        # Conectar sinais
        self.signals.show_popup.connect(self._show_popup_slot)
        self.signals.update_popup.connect(self._update_popup_slot)
        self.signals.close_popup.connect(self._close_popup_slot)
        self.signals.insert_text.connect(self._insert_text_slot)
        
    def start(self):
        self.listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release
        )
        self.listener.start()
    
    def on_key_press(self, key):
        try:
            # Detectar Alt
            if key == Key.alt_l or key == Key.alt_r or key == Key.alt:
                self.alt_pressed = True
                return
            
            # Se estiver no modo de busca
            if self.search_mode:
                if key == Key.right or key == Key.enter:
                    if self.templates_popup:
                        current_item = self.templates_popup.list_widget.currentItem()
                        if current_item:
                            texto = current_item.data(Qt.ItemDataRole.UserRole)
                            if texto:
                                chars_to_delete = 2 + len(self.search_query)
                                self.signals.insert_text.emit(texto, chars_to_delete)
                    return
                elif key == Key.esc or key == Key.space:
                    self.cancel_search()
                    return
                elif key == Key.backspace:
                    if len(self.search_query) > 0:
                        self.search_query = self.search_query[:-1]
                        self.signals.update_popup.emit(self.search_query)
                    else:
                        self.cancel_search()
                    return
                elif key == Key.up:
                    if self.templates_popup:
                        self.templates_popup.select_previous()
                    return
                elif key == Key.down:
                    if self.templates_popup:
                        self.templates_popup.select_next()
                    return
                elif hasattr(key, 'char') and key.char:
                    self.search_query += key.char
                    self.signals.update_popup.emit(self.search_query)
                    return
            
            # Verificar atalhos Alt+Tecla
            if self.alt_pressed and hasattr(key, 'char') and key.char:
                self.check_alt_shortcuts(key.char.upper())
                return
            
            # Modo normal - detectar "//"
            if hasattr(key, 'char') and key.char:
                self.typed_text += key.char
                
                if self.typed_text.endswith('//'):
                    cursor_pos = QCursor.pos()
                    self.signals.show_popup.emit(cursor_pos.x(), cursor_pos.y())
                    return
                
                if len(self.typed_text) > 30:
                    self.typed_text = self.typed_text[-30:]
            
            elif key == Key.space:
                self.check_text_shortcuts()
                self.typed_text = ""
            
            elif key in [Key.enter, Key.tab]:
                self.typed_text = ""
                
        except Exception as e:
            print(f"Erro no listener: {e}")
    
    def on_key_release(self, key):
        try:
            if key == Key.alt_l or key == Key.alt_r or key == Key.alt:
                self.alt_pressed = False
        except Exception as e:
            print(f"Erro no release: {e}")
    
    def _show_popup_slot(self, x, y):
        print("=== INICIANDO BUSCA (via signal) ===")
        self.search_mode = True
        self.search_query = ""
        
        if self.templates_popup:
            try:
                self.templates_popup.close()
            except:
                pass
        
        self.templates_popup = TemplatesPopup(self.db, self)
        
        # Posicionar próximo ao mouse (onde provavelmente está o cursor de texto)
        popup_x = x + 10  # Pequeno offset para não cobrir o texto
        popup_y = y - self.templates_popup.height() - 5  # Acima do cursor
        
        # Verificar limites da tela
        screen = QApplication.primaryScreen().geometry()
        
        # Ajustar se sair da tela pela direita
        if popup_x + self.templates_popup.width() > screen.width() - 10:
            popup_x = screen.width() - self.templates_popup.width() - 10
        
        # Ajustar se sair pela esquerda
        if popup_x < 10:
            popup_x = 10
        
        # Se não couber acima, mostrar abaixo
        if popup_y < 10:
            popup_y = y + 25
        
        print(f"Popup na posição do mouse: {popup_x}, {popup_y}")
        self.templates_popup.move(popup_x, popup_y)
        self.templates_popup.show()
        self.templates_popup.raise_()
        self.templates_popup.activateWindow()
        
        print(f"Popup visível? {self.templates_popup.isVisible()}")
    
    def _update_popup_slot(self, query):
        if self.templates_popup:
            self.templates_popup.update_search(query)
    
    def _close_popup_slot(self):
        if self.templates_popup:
            self.templates_popup.close()
            self.templates_popup = None
    
    def _insert_text_slot(self, texto, chars_to_delete):
        print(f"Inserindo texto: {texto[:30]}...")
        
        # Resetar estado
        self.search_mode = False
        self.search_query = ""
        self.typed_text = ""
        
        # Fechar popup se ainda estiver aberto
        if self.templates_popup:
            try:
                self.templates_popup.close()
            except:
                pass
            self.templates_popup = None
        
        # Apagar e digitar em thread separada
        def digitar():
            try:
                time.sleep(0.05)
                
                # Apagar
                for i in range(chars_to_delete):
                    self.keyboard_controller.press(Key.backspace)
                    self.keyboard_controller.release(Key.backspace)
                    time.sleep(0.003)
                
                time.sleep(0.05)
                
                # Digitar com Shift+Enter para quebras de linha
                for linha in texto.split('\n'):
                    if linha:  # Se a linha não está vazia
                        self.keyboard_controller.type(linha)
                    # Pressionar Shift+Enter para quebra de linha (exceto na última linha)
                    if linha != texto.split('\n')[-1] or texto.endswith('\n'):
                        with self.keyboard_controller.pressed(Key.shift):
                            self.keyboard_controller.press(Key.enter)
                            self.keyboard_controller.release(Key.enter)
                        time.sleep(0.05)
                
                print("Concluído!")
                
            except Exception as e:
                print(f"Erro ao digitar: {e}")
        
        import threading
        thread = threading.Thread(target=digitar)
        thread.daemon = True
        thread.start()
    
    def cancel_search(self):
        print("Cancelando busca")
        self.search_mode = False
        self.search_query = ""
        self.typed_text = ""  # Resetar o buffer completamente
        
        # Fechar popup
        self.signals.close_popup.emit()
    
    def check_text_shortcuts(self):
        print(f"=== check_text_shortcuts chamado ===")
        print(f"Texto digitado: '{self.typed_text}'")
        
        if not self.typed_text.strip():
            print("Texto vazio, ignorando")
            return
        
        # Verificar templates com atalho de texto
        templates = self.db.get_templates()
        print(f"Verificando {len(templates)} templates...")
        for template in templates:
            if template[3]:
                print(f"  Template '{template[1]}' tem atalho: '{template[3]}'")
                if template[3].lower() == self.typed_text.strip().lower():
                    print(f"  -> MATCH! Executando template")
                    for _ in range(len(self.typed_text) + 1):
                        self.keyboard_controller.press(Key.backspace)
                        self.keyboard_controller.release(Key.backspace)
                        time.sleep(0.01)
                    
                    time.sleep(0.05)
                    
                    # Digitar com Shift+Enter para quebras de linha
                    texto = template[2]
                    for linha in texto.split('\n'):
                        if linha:  # Se a linha não está vazia
                            self.keyboard_controller.type(linha)
                        # Pressionar Shift+Enter para quebra de linha (exceto na última linha)
                        if linha != texto.split('\n')[-1] or texto.endswith('\n'):
                            with self.keyboard_controller.pressed(Key.shift):
                                self.keyboard_controller.press(Key.enter)
                                self.keyboard_controller.release(Key.enter)
                            time.sleep(0.05)
                    
                    return
        
        # Verificar shortcuts com atalho de texto
        shortcuts = self.db.get_shortcuts()
        print(f"Verificando {len(shortcuts)} shortcuts...")
        for shortcut in shortcuts:
            print(f"  Shortcut '{shortcut['nome']}':")
            print(f"    - Ativo: {shortcut['ativo']}")
            print(f"    - Tecla: '{shortcut.get('tecla_atalho', '')}'")
            
            if not shortcut['ativo']:
                print(f"    -> Desativado, pulando")
                continue
            
            tecla = shortcut.get('tecla_atalho', '')
            print(f"    - Comparando '{tecla}' com '{self.typed_text.strip()}'")
            
            # Se tem mais de 2 caracteres, é atalho de texto
            if len(tecla) > 2:
                if tecla.lower() == self.typed_text.strip().lower():
                    print(f"    -> MATCH! Executando shortcut com {len(shortcut['acoes'])} ações")
                    # Apagar texto digitado
                    for _ in range(len(self.typed_text) + 1):
                        self.keyboard_controller.press(Key.backspace)
                        self.keyboard_controller.release(Key.backspace)
                        time.sleep(0.01)
                    
                    # Executar ações do shortcut
                    self.execute_shortcut(shortcut['acoes'])
                    return
            else:
                print(f"    -> Tecla muito curta ({len(tecla)} chars), é Alt+Tecla")
        
        print("=== Nenhum match encontrado ===")
    
    def check_alt_shortcuts(self, char):
        print(f"Verificando Alt+{char}")
        shortcuts = self.db.get_shortcuts()
        for shortcut in shortcuts:
            if not shortcut['ativo']:
                continue
            
            tecla = shortcut.get('tecla_atalho', '')
            # Se tem 1-2 caracteres, é Alt+Tecla
            if len(tecla) <= 2 and tecla.upper() == char.upper():
                print(f"Executando shortcut: {shortcut['nome']}")
                
                # IMPORTANTE: Soltar Alt antes de executar
                def execute_delayed():
                    time.sleep(0.1)  # Aguardar Alt ser solto
                    self.execute_shortcut(shortcut['acoes'])
                
                import threading
                thread = threading.Thread(target=execute_delayed)
                thread.daemon = True
                thread.start()
                return
    
    def execute_shortcut(self, acoes):
        print(f"Executando {len(acoes)} ações...")
        
        def run():
            try:
                time.sleep(0.1)
                for i, acao in enumerate(acoes):
                    print(f"Ação {i+1}: {acao['type']}")
                    
                    if acao['type'] == 'click':
                        vezes = acao.get('vezes', 1)
                        for _ in range(vezes):
                            self.mouse_controller.position = (acao['x'], acao['y'])
                            self.mouse_controller.click(Button.left, 1)
                            if vezes > 1:
                                time.sleep(0.1)  # Pequeno delay entre cliques múltiplos
                    
                    elif acao['type'] == 'right_click':
                        self.mouse_controller.position = (acao['x'], acao['y'])
                        self.mouse_controller.click(Button.right, 1)
                    
                    elif acao['type'] == 'drag':
                        # Mover para posição inicial
                        self.mouse_controller.position = (acao['x1'], acao['y1'])
                        time.sleep(0.1)
                        
                        # Pressionar botão
                        self.mouse_controller.press(Button.left)
                        time.sleep(0.05)
                        
                        # Arrastar para posição final
                        self.mouse_controller.position = (acao['x2'], acao['y2'])
                        time.sleep(0.1)
                        
                        # Soltar botão
                        self.mouse_controller.release(Button.left)
                        
                    elif acao['type'] == 'type':
                        # Digitar texto, usando Shift+Enter para quebras de linha
                        texto = acao['text']
                        for linha in texto.split('\n'):
                            if linha:  # Se a linha não está vazia
                                self.keyboard_controller.type(linha)
                            # Pressionar Shift+Enter para quebra de linha (exceto na última linha)
                            if linha != texto.split('\n')[-1] or texto.endswith('\n'):
                                with self.keyboard_controller.pressed(Key.shift):
                                    self.keyboard_controller.press(Key.enter)
                                    self.keyboard_controller.release(Key.enter)
                                time.sleep(0.05)
                        
                    elif acao['type'] == 'sleep':
                        time.sleep(acao['ms'] / 1000.0)
                    
                    time.sleep(0.05)
                
                print("Ações concluídas!")
            except Exception as e:
                print(f"Erro ao executar ações: {e}")
        
        import threading
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()


class TemplatesPopup(QWidget):
    def __init__(self, db, listener):
        super().__init__()
        self.db = db
        self.listener = listener
        self.current_templates = []
        self.selected_index = 0
        self.init_ui()
    
    def init_ui(self):
        print(">> Criando popup")
        
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Container principal
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 2px solid #ddd;
                border-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # Título com busca
        self.title_label = QLabel('// (digite para buscar)')
        self.title_label.setStyleSheet("""
            QLabel {
                color: #999;
                font-size: 11px;
                padding: 5px;
                background: transparent;
            }
        """)
        layout.addWidget(self.title_label)
        
        # Lista de templates
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                border: none;
                background: white;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 10px;
                border-radius: 4px;
                margin: 2px 0px;
                color: #333;
                background-color: white;
            }
            QListWidget::item:selected {
                background-color: #2196F3;
                color: white;
            }
            QListWidget::item:hover {
                background-color: #E3F2FD;
                color: #333;
            }
        """)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        layout.addWidget(self.list_widget)
        
        # Info
        info = QLabel('→ Seta direita para inserir  |  ESC para cancelar')
        info.setStyleSheet("""
            QLabel {
                color: #999;
                font-size: 10px;
                padding: 5px;
                background: transparent;
            }
        """)
        layout.addWidget(info)
        
        # Layout principal
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        
        self.setFixedWidth(400)
        self.setFixedHeight(300)
        
        # Carregar todos os templates inicialmente
        self.update_search("")
        
        print(">> Popup criado")
    
    def update_search(self, query):
        self.list_widget.clear()
        
        if query:
            self.title_label.setText(f'// {query}')
            self.current_templates = self.db.search_templates(query)
        else:
            self.title_label.setText('// (digite para buscar)')
            self.current_templates = self.db.get_templates()
        
        if self.current_templates:
            for template in self.current_templates:
                item = QListWidgetItem()
                
                nome = template[1]
                preview = template[2][:50] + '...' if len(template[2]) > 50 else template[2]
                
                item.setText(f"{nome}\n{preview}")
                item.setData(Qt.ItemDataRole.UserRole, template[2])
                
                self.list_widget.addItem(item)
            
            self.list_widget.setCurrentRow(0)
            self.selected_index = 0
        else:
            item = QListWidgetItem("Nenhum template encontrado")
            self.list_widget.addItem(item)
    
    def select_next(self):
        if self.list_widget.count() > 0:
            current = self.list_widget.currentRow()
            next_row = min(current + 1, self.list_widget.count() - 1)
            self.list_widget.setCurrentRow(next_row)
            self.selected_index = next_row
    
    def select_previous(self):
        if self.list_widget.count() > 0:
            current = self.list_widget.currentRow()
            prev_row = max(current - 1, 0)
            self.list_widget.setCurrentRow(prev_row)
            self.selected_index = prev_row
    
    def on_item_clicked(self, item):
        texto = item.data(Qt.ItemDataRole.UserRole)
        if texto:
            print("Template clicado no popup")
            chars_to_delete = 2 + len(self.listener.search_query)
            
            # Resetar estado do listener
            self.listener.search_mode = False
            self.listener.search_query = ""
            self.listener.typed_text = ""
            
            # Fechar popup
            self.close()
            
            # Agendar inserção do texto
            QTimer.singleShot(50, lambda: self.listener.signals.insert_text.emit(texto, chars_to_delete))


class FloatingCircle(QWidget):
    def __init__(self, db, firebase, user_data):
        super().__init__()
        self.db = db
        self.firebase = firebase
        self.user_data = user_data
        
        print("DEBUG FloatingCircle: __init__ chamado")
        print(f"DEBUG: db = {db}")
        print(f"DEBUG: firebase = {firebase}")
        print(f"DEBUG: user_data = {user_data}")
        
        self.dragging = False
        self.dragging = False
        self.drag_start_position = QPoint()
        self.click_position = QPoint()
        self.menu = None
        self.menu_open = False  # Controlar se menu está aberto
        
        # Propriedades para animação
        # self._scale = 0.85  # Iniciar 15% menor (85%)
        # self._opacity_value = 0.6  # Iniciar com 60% opacidade

        # Propriedades para animação (TESTE: começar 100% visível)
        self._scale = 1.0  # Tamanho normal
        self._opacity_value = 1.0  # 100% visível
        
        self.init_ui()
        
        # Criar animações
        self.scale_animation = QPropertyAnimation(self, b"scale")
        self.scale_animation.setDuration(200)  # 200ms
        self.scale_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.opacity_animation = QPropertyAnimation(self, b"opacity_value")
        self.opacity_animation.setDuration(200)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    
    # Propriedades animáveis
    @pyqtProperty(float)
    def scale(self):
        return self._scale
    
    @scale.setter
    def scale(self, value):
        self._scale = value
        self.update()  # Redesenhar
    
    @pyqtProperty(float)
    def opacity_value(self):
        return self._opacity_value
    
    @opacity_value.setter
    def opacity_value(self, value):
        self._opacity_value = value
        self.setWindowOpacity(value)
    
    def init_ui(self):
        print("DEBUG FloatingCircle: init_ui chamado")

        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.setFixedSize(80, 80)
        
        # Opacidade inicial: 60%
        # self.setWindowOpacity(0.6)

        # Opacidade inicial: 100% (TESTE)
        self.setWindowOpacity(1.0)
        
        # SEMPRE iniciar no canto superior direito (ignorar posição salva)
        screen = QApplication.primaryScreen().geometry()
        print(f"DEBUG: Resolução da tela: {screen.width()}x{screen.height()}")

        # Forçar posição visível e segura
        x = screen.width() - 120  # Mais espaço da borda
        y = 50  # Mais abaixo do topo
        self.move(x, y)

        # CRÍTICO: Garantir que fica sempre no topo
        self.raise_()
        self.activateWindow()
        self.setFocus()

        print("DEBUG FloatingCircle: Janela configurada")
        print(f"DEBUG: Posição: ({x}, {y})")
        print(f"DEBUG: Tamanho: {self.size()}")
        print(f"DEBUG: Flags: {self.windowFlags()}")

    
    def paintEvent(self, event):
        """Desenhar o círculo com anéis concêntricos"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Aplicar escala no centro
        center_x = self.width() / 2
        center_y = self.height() / 2
        
        painter.translate(center_x, center_y)
        painter.scale(self._scale, self._scale)
        painter.translate(-center_x, -center_y)
        
        # Fundo externo gradiente (sombra suave)
        gradient = QRadialGradient(center_x, center_y, 40)
        gradient.setColorAt(0, QColor(60, 60, 60, 150))
        gradient.setColorAt(0.7, QColor(40, 40, 40, 100))
        gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 80, 80)
        
        # Anel externo (cinza escuro) - espaçamento reduzido
        painter.setPen(QPen(QColor(80, 80, 80), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(8, 8, 64, 64)
        
        # Anel médio (cinza médio) - mais próximo
        painter.setPen(QPen(QColor(120, 120, 120), 2.5))
        painter.drawEllipse(12, 12, 56, 56)  # Era 14,14,52,52
        
        # Anel interno (cinza claro) - mais próximo
        painter.setPen(QPen(QColor(160, 160, 160), 2))
        painter.drawEllipse(16, 16, 48, 48)  # Era 19,19,42,42
        
        # Centro branco - levemente maior
        gradient_center = QRadialGradient(center_x, center_y, 21)  # Era 18
        gradient_center.setColorAt(0, QColor(255, 255, 255, 255))
        gradient_center.setColorAt(0.8, QColor(240, 240, 240, 255))
        gradient_center.setColorAt(1, QColor(220, 220, 220, 255))
        painter.setBrush(gradient_center)
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawEllipse(20, 20, 40, 40)  # Era 22,22,36,36
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.click_position = event.globalPosition().toPoint()
            self.drag_start_position = event.globalPosition().toPoint() - self.pos()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            moved_distance = (event.globalPosition().toPoint() - self.click_position).manhattanLength()
            
            if moved_distance > 5:
                self.dragging = True
                new_pos = event.globalPosition().toPoint() - self.drag_start_position
                self.move(new_pos)
            
            event.accept()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                # Foi arraste - posição não é mais salva, sempre reinicia no canto
                # pos = self.pos()
                # self.db.save_position(pos.x(), pos.y())
                pass
            else:
                # Foi clique - abrir menu
                self.show_menu()
            
            self.dragging = False
            event.accept()
    
    def show_menu(self):
        print(f"Círculo: show_menu chamado. Menu existe? {self.menu is not None}")
        if self.menu:
            print(f"Círculo: Menu está visível? {self.menu.isVisible()}")
        
        # Se o menu já está aberto, fechar com animação reversa
        if self.menu and self.menu.isVisible():
            print("Círculo: Menu já está aberto, fechando com animação...")
            
            try:
                # Animação reversa: slide de volta para o círculo + fade out
                circle_pos = QPoint(self.x(), self.y())
                
                self.menu_close_slide = QPropertyAnimation(self.menu, b"pos")
                self.menu_close_slide.setDuration(200)
                self.menu_close_slide.setStartValue(self.menu.pos())
                self.menu_close_slide.setEndValue(circle_pos)
                self.menu_close_slide.setEasingCurve(QEasingCurve.Type.InCubic)
                
                self.menu_close_fade = QPropertyAnimation(self.menu, b"windowOpacity")
                self.menu_close_fade.setDuration(200)
                self.menu_close_fade.setStartValue(1.0)
                self.menu_close_fade.setEndValue(0.0)
                
                # Quando terminar, fechar de verdade
                def on_close_finished():
                    if self.menu:
                        self.menu.close()
                        self.menu = None
                    self.menu_open = False
                
                self.menu_close_fade.finished.connect(on_close_finished)
                
                self.menu_close_slide.start()
                self.menu_close_fade.start()
                
                print("Círculo: Animação de fechamento iniciada")
            except Exception as e:
                # Se animação falhar, fechar normalmente
                print(f"Erro na animação de fechamento: {e}")
                self.menu.close()
                self.menu = None
                self.menu_open = False
            
            # Círculo volta para 85% e 60%
            self.scale_animation.stop()
            self.scale_animation.setDuration(200)
            self.scale_animation.setStartValue(self._scale)
            self.scale_animation.setEndValue(0.85)
            self.scale_animation.start()
            
            self.opacity_animation.stop()
            self.opacity_animation.setDuration(200)
            self.opacity_animation.setStartValue(self._opacity_value)
            self.opacity_animation.setEndValue(0.6)
            self.opacity_animation.start()
            return
        
        # Abrir novo menu
        print("Círculo: Abrindo novo menu...")
        if self.menu:
            self.menu.close()
        
        self.menu_open = True
        
        # Criar menu
        self.menu = MainMenu(self.db, self, self.firebase, self.user_data)
        
        # Conectar evento de fechar
        def on_menu_closed():
            self.menu = None
            self.menu_open = False
        
        self.menu.destroyed.connect(on_menu_closed)
        
        # Posição final do menu
        menu_x = self.x() - 360
        menu_y = self.y()
        
        # Tentar animação, mas garantir que menu apareça
        try:
            # Começar na posição do círculo
            circle_pos = QPoint(self.x(), self.y())
            final_pos = QPoint(menu_x, menu_y)
            
            self.menu.move(circle_pos)
            self.menu.setWindowOpacity(0.0)
            self.menu.show()
            self.menu.raise_()
            
            # Animação: slide para esquerda + fade in
            self.menu_slide_anim = QPropertyAnimation(self.menu, b"pos")
            self.menu_slide_anim.setDuration(250)
            self.menu_slide_anim.setStartValue(circle_pos)
            self.menu_slide_anim.setEndValue(final_pos)
            self.menu_slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            
            self.menu_fade_anim = QPropertyAnimation(self.menu, b"windowOpacity")
            self.menu_fade_anim.setDuration(250)
            self.menu_fade_anim.setStartValue(0.0)
            self.menu_fade_anim.setEndValue(1.0)
            
            self.menu_slide_anim.start()
            self.menu_fade_anim.start()
            
            print("Círculo: Menu animação iniciada")
        except Exception as e:
            # Se animação falhar, mostrar menu normalmente
            print(f"Erro na animação: {e}")
            self.menu.move(menu_x, menu_y)
            self.menu.setWindowOpacity(1.0)
            self.menu.show()
            self.menu.raise_()
        
        # Animações do círculo
        self.scale_animation.stop()
        self.scale_animation.setDuration(150)
        self.scale_animation.setStartValue(self._scale)
        self.scale_animation.setEndValue(1.0)
        self.scale_animation.start()
        
        self.opacity_animation.stop()
        self.opacity_animation.setDuration(150)
        self.opacity_animation.setStartValue(self._opacity_value)
        self.opacity_animation.setEndValue(1.0)
        self.opacity_animation.start()
    
    def enterEvent(self, event):
        """Mouse entrou no círculo - aumentar escala e opacidade"""
        if not self.menu_open:
            # Animar para tamanho normal (100%) e opaco
            self.scale_animation.stop()
            self.scale_animation.setStartValue(self._scale)
            self.scale_animation.setEndValue(1.0)
            self.scale_animation.start()
            
            self.opacity_animation.stop()
            self.opacity_animation.setStartValue(self._opacity_value)
            self.opacity_animation.setEndValue(1.0)
            self.opacity_animation.start()
    
    def leaveEvent(self, event):
        """Mouse saiu do círculo - voltar para escala/opacidade reduzida"""
        if not self.menu_open:
            # Animar para 85% e 60% opacidade
            self.scale_animation.stop()
            self.scale_animation.setStartValue(self._scale)
            self.scale_animation.setEndValue(0.85)
            self.scale_animation.start()
            
            self.opacity_animation.stop()
            self.opacity_animation.setStartValue(self._opacity_value)
            self.opacity_animation.setEndValue(0.6)
            self.opacity_animation.start()


class MainMenu(QWidget):
    _last_tab = 'templates'  # Variável de classe para lembrar última aba
    
    def __init__(self, db, parent=None, firebase=None, user_data=None):
        super().__init__(parent)
        self.db = db
        self.circle_parent = parent
        self.firebase = firebase
        self.user_data = user_data
        self.add_window = None  # Manter referência
        self.init_ui()
    
    def closeEvent(self, event):
        """Ao fechar menu, resetar círculo com animação"""
        if self.circle_parent:
            self.circle_parent.menu_open = False
            # Animação de volta para 85% e 60%
            self.circle_parent.scale_animation.stop()
            self.circle_parent.scale_animation.setDuration(200)
            self.circle_parent.scale_animation.setStartValue(self.circle_parent._scale)
            self.circle_parent.scale_animation.setEndValue(0.85)
            self.circle_parent.scale_animation.start()
            
            self.circle_parent.opacity_animation.stop()
            self.circle_parent.opacity_animation.setDuration(200)
            self.circle_parent.opacity_animation.setStartValue(self.circle_parent._opacity_value)
            self.circle_parent.opacity_animation.setEndValue(0.6)
            self.circle_parent.opacity_animation.start()
        event.accept()
    
    def hideEvent(self, event):
        """Ao esconder menu, também resetar círculo com animação"""
        if self.circle_parent:
            self.circle_parent.menu_open = False
            # Animação de volta para 85% e 60%
            self.circle_parent.scale_animation.stop()
            self.circle_parent.scale_animation.setDuration(200)
            self.circle_parent.scale_animation.setStartValue(self.circle_parent._scale)
            self.circle_parent.scale_animation.setEndValue(0.85)
            self.circle_parent.scale_animation.start()
            
            self.circle_parent.opacity_animation.stop()
            self.circle_parent.opacity_animation.setDuration(200)
            self.circle_parent.opacity_animation.setStartValue(self.circle_parent._opacity_value)
            self.circle_parent.opacity_animation.setEndValue(0.6)
            self.circle_parent.opacity_animation.start()
        event.accept()
    
    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Popup  # Popup fecha automaticamente ao clicar fora
        )
        
        self.setFixedWidth(350)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        tabs_container = QWidget()
        tabs_layout = QHBoxLayout()
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(2)
        
        self.btn_templates = QPushButton('Templates')
        self.btn_atalhos = QPushButton('Atalhos')
        self.btn_config = QPushButton('⚙️')  # Botão de config

        self.btn_templates.clicked.connect(self.show_templates_tab)
        self.btn_atalhos.clicked.connect(self.show_atalhos_tab)
        self.btn_config.clicked.connect(self.show_config_tab)

        # Estilo do botão config (menor, ícone)
        self.btn_config.setFixedWidth(50)

        tabs_layout.addWidget(self.btn_templates)
        tabs_layout.addWidget(self.btn_atalhos)
        tabs_layout.addWidget(self.btn_config)
        
        # Espaçamento menor antes do botão sair
        tabs_layout.addSpacing(20)
        
        # Botão sair no canto direito
        btn_sair = QPushButton('Sair')
        btn_sair.setFixedWidth(60)
        btn_sair.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #82414c;
                border: none;
                padding: 12px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(130, 65, 76, 0.15);
                color: #82414c;
            }
        """)
        btn_sair.clicked.connect(self.sair_programa)
        tabs_layout.addWidget(btn_sair)
        
        tabs_container.setLayout(tabs_layout)
        
        main_layout.addWidget(tabs_container)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #f5f0ed; }")
        
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(5)
        self.content_widget.setLayout(self.content_layout)
        
        scroll.setWidget(self.content_widget)
        main_layout.addWidget(scroll)
        
        self.setLayout(main_layout)
        
        self.setStyleSheet("""
            QWidget {
                background-color: #f5f0ed;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #c8bfb8;
                color: #3d3d3d;
                border: none;
                padding: 12px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b8ada6;
            }
        """)
        
        self.setFixedHeight(400)
        
        # Abrir na última aba usada (do banco ou variável de classe)
        last_tab = self.db.get_config('last_tab', MainMenu._last_tab)
        if last_tab == 'atalhos':
            self.show_atalhos_tab()
        else:
            self.show_templates_tab()
    
    def show_templates_tab(self):
        MainMenu._last_tab = 'templates'
        self.db.set_config('last_tab', 'templates')
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

                # Sub-abas: Meus Templates / Templates do Setor
                sub_tabs = QWidget()
                sub_layout = QHBoxLayout()
                sub_layout.setContentsMargins(0, 0, 0, 0)
                sub_layout.setSpacing(5)
                
                self.btn_meus_templates = QPushButton('Meus Templates')
                self.btn_setor_templates = QPushButton('Templates do Setor')
                
                self.btn_meus_templates.clicked.connect(lambda: self.show_templates_list(apenas_meus=True))
                self.btn_setor_templates.clicked.connect(lambda: self.show_templates_list(apenas_meus=False))
                
                sub_layout.addWidget(self.btn_meus_templates)
                sub_layout.addWidget(self.btn_setor_templates)
                sub_tabs.setLayout(sub_layout)
                
                self.content_layout.addWidget(sub_tabs)
                
                # Mostrar templates do setor por padrão
                self.show_templates_list(apenas_meus=False)

    def show_templates_list(self, apenas_meus=False):
        """Mostrar lista de templates (meus ou do setor)"""
        # Remover conteúdo antigo (exceto os botões de sub-aba)
        while self.content_layout.count() > 1:  # Manter primeiro widget (sub-abas)
            child = self.content_layout.takeAt(1)
            if child.widget():
                child.widget().deleteLater()
        
        # Atualizar estilo dos botões
        if apenas_meus:
            self.btn_meus_templates.setStyleSheet("""
                QPushButton {
                    background-color: #406e54;
                    color: white;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
            self.btn_setor_templates.setStyleSheet("""
                QPushButton {
                    background-color: #e0e0e0;
                    color: #666;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                }
            """)
        else:
            self.btn_meus_templates.setStyleSheet("""
                QPushButton {
                    background-color: #e0e0e0;
                    color: #666;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                }
            """)
            self.btn_setor_templates.setStyleSheet("""
                QPushButton {
                    background-color: #406e54;
                    color: white;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        
        # Busca
        search_container = QWidget()
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 10, 0, 10)
        
        search_input = QLineEdit()
        search_input.setPlaceholderText('🔍 Buscar templates...')
        search_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 2px solid #88c22b;
            }
        """)
        search_input.textChanged.connect(lambda text: self.filter_templates(text, apenas_meus))
        search_layout.addWidget(search_input)
        
        search_container.setLayout(search_layout)
        self.content_layout.addWidget(search_container)
        
        # Lista de templates
        self.current_templates_list = QWidget()
        templates_layout = QVBoxLayout()
        templates_layout.setSpacing(8)
        
        templates = self.db.get_templates(apenas_meus=apenas_meus)
        
        if not templates:
            empty_label = QLabel('Nenhum template encontrado' if apenas_meus else 'Nenhum template no setor')
            empty_label.setStyleSheet('color: #999; font-style: italic; padding: 20px;')
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            templates_layout.addWidget(empty_label)
        else:
            for template in templates:
                # Aqui você vai usar o mesmo código que já tinha para renderizar cada template
                # Copie o código do template card que já existia
                pass  # Por enquanto vazio, vou te ajudar no próximo passo
        
        self.current_templates_list.setLayout(templates_layout)
        self.content_layout.addWidget(self.current_templates_list)
        
        # Botão adicionar
        btn_add = QPushButton('+ Novo Template')
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: #88c22b;
                color: white;
                border: none;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #76a824;
            }
        """)
        btn_add.clicked.connect(self.add_template)
        self.content_layout.addWidget(btn_add)

    def filter_templates(self, query, apenas_meus):
        """Filtrar templates conforme busca"""
        if query.strip():
            templates = self.db.search_templates(query, apenas_meus=apenas_meus)
        else:
            templates = self.db.get_templates(apenas_meus=apenas_meus)
        
        # Recriar lista com resultados filtrados
        self.show_templates_list(apenas_meus=apenas_meus)
    
    def on_template_selection_changed(self, template_id, state):
        """Callback quando checkbox de template é alterado"""
        if state == Qt.CheckState.Checked.value:
            if template_id not in self.selected_templates:
                self.selected_templates.append(template_id)
        else:
            if template_id in self.selected_templates:
                self.selected_templates.remove(template_id)
        
        # Mostrar/ocultar botão de deletar selecionados
        if len(self.selected_templates) > 0:
            self.btn_delete_selected_templates.show()
        else:
            self.btn_delete_selected_templates.hide()
    
    def delete_selected_templates(self):
        """Deletar templates selecionados"""
        if not self.selected_templates:
            return
        
        msg = QMessageBox(self)
        msg.setWindowTitle('Confirmar exclusão')
        msg.setText(f'Deseja realmente deletar {len(self.selected_templates)} template(s)?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        msg.setStyleSheet("""
            QMessageBox {
                background-color: white;
            }
            QMessageBox QLabel {
                color: #2d2d2d;
                font-size: 13px;
            }
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            for template_id in self.selected_templates:
                self.db.delete_template(template_id)
            self.selected_templates.clear()
            self.show_templates_tab()
    
    def delete_template(self, template_id):
        msg = QMessageBox()
        msg.setWindowTitle('Confirmar exclusão')
        msg.setText('Deseja realmente deletar este template?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        msg.setStyleSheet("""
            QMessageBox {
                background-color: white;
            }
            QMessageBox QLabel {
                color: #2d2d2d;
                font-size: 13px;
            }
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        
        result = msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.db.delete_template(template_id)
            self.show_templates_tab()
        
        # Reabrir menu
        self.show()
        self.raise_()
        self.activateWindow()
    
    def edit_template(self, template_id, nome, texto, atalho):
        """Abrir janela para editar template existente"""
        print(f"Editando template {template_id}")
        if self.add_window and self.add_window.isVisible():
            self.add_window.close()
        
        self.add_window = EditTemplateWindow(self.db, menu_ref=self, template_id=template_id, nome=nome, texto=texto, atalho=atalho)
        self.add_window.show()
        self.add_window.raise_()
        self.add_window.activateWindow()
    
    def show_atalhos_tab(self):
        MainMenu._last_tab = 'atalhos'
        self.db.set_config('last_tab', 'atalhos')
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

                # Sub-abas: Meus Atalhos / Atalhos do Setor
                sub_tabs = QWidget()
                sub_layout = QHBoxLayout()
                sub_layout.setContentsMargins(0, 0, 0, 0)
                sub_layout.setSpacing(5)
                
                self.btn_meus_atalhos = QPushButton('Meus Atalhos')
                self.btn_setor_atalhos = QPushButton('Atalhos do Setor')
                
                self.btn_meus_atalhos.clicked.connect(lambda: self.show_atalhos_list(apenas_meus=True))
                self.btn_setor_atalhos.clicked.connect(lambda: self.show_atalhos_list(apenas_meus=False))
                
                sub_layout.addWidget(self.btn_meus_atalhos)
                sub_layout.addWidget(self.btn_setor_atalhos)
                sub_tabs.setLayout(sub_layout)
                
                self.content_layout.addWidget(sub_tabs)
                
                # Mostrar atalhos do setor por padrão
                self.show_atalhos_list(apenas_meus=False)
    
    def show_atalhos_list(self, apenas_meus=False):
        """Mostrar lista de atalhos (meus ou do setor)"""
        # Remover conteúdo antigo (exceto sub-abas)
        while self.content_layout.count() > 1:
            child = self.content_layout.takeAt(1)
            if child.widget():
                child.widget().deleteLater()
        
        # Atualizar estilo dos botões
        if apenas_meus:
            self.btn_meus_atalhos.setStyleSheet("""
                QPushButton {
                    background-color: #406e54;
                    color: white;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
            self.btn_setor_atalhos.setStyleSheet("""
                QPushButton {
                    background-color: #e0e0e0;
                    color: #666;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                }
            """)
        else:
            self.btn_meus_atalhos.setStyleSheet("""
                QPushButton {
                    background-color: #e0e0e0;
                    color: #666;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                }
            """)
            self.btn_setor_atalhos.setStyleSheet("""
                QPushButton {
                    background-color: #406e54;
                    color: white;
                    border: none;
                    padding: 8px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        
        # Lista de atalhos
        shortcuts = self.db.get_shortcuts(apenas_meus=apenas_meus)
        
        if not shortcuts:
            empty_label = QLabel('Nenhum atalho encontrado' if apenas_meus else 'Nenhum atalho no setor')
            empty_label.setStyleSheet('color: #999; font-style: italic; padding: 20px;')
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(empty_label)
        else:
            for shortcut in shortcuts:
                # Card do atalho (copie o código que já existia)
                # Vou te dar no próximo passo
                pass
        
        # Botão adicionar
        btn_add = QPushButton('+ Novo Atalho')
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: #88c22b;
                color: white;
                border: none;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #76a824;
            }
        """)
        btn_add.clicked.connect(self.add_shortcut)
        self.content_layout.addWidget(btn_add)

    def show_config_tab(self):
        """Mostrar aba de configurações"""
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Atualizar estilo dos botões
        self.btn_templates.setStyleSheet("""
            QPushButton {
                background-color: #c8bfb8;
                color: #3d3d3d;
                border: none;
                padding: 12px;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        self.btn_atalhos.setStyleSheet("""
            QPushButton {
                background-color: #c8bfb8;
                color: #3d3d3d;
                border: none;
                padding: 12px;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        self.btn_config.setStyleSheet("""
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 12px;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        # Título
        titulo = QLabel('⚙️ Configurações')
        titulo.setStyleSheet('font-size: 18px; font-weight: bold; color: #2d2d2d; padding: 10px;')
        self.content_layout.addWidget(titulo)
        
        # Info do usuário
        user_card = QWidget()
        user_layout = QVBoxLayout()
        user_layout.setContentsMargins(15, 15, 15, 15)
        user_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 8px;
                border: 2px solid #e0e0e0;
            }
        """)
        
        nome_label = QLabel(f"👤 {self.user_data['nome']}")
        nome_label.setStyleSheet('font-size: 14px; font-weight: bold; color: #2d2d2d;')
        user_layout.addWidget(nome_label)
        
        setor_label = QLabel(f"🏢 Setor: {self.user_data['setor']}")
        setor_label.setStyleSheet('font-size: 12px; color: #666;')
        user_layout.addWidget(setor_label)
        
        user_card.setLayout(user_layout)
        self.content_layout.addWidget(user_card)
        
        # Opções
        self.content_layout.addSpacing(10)
        
        # Toggle de animações
        anim_container = QWidget()
        anim_layout = QHBoxLayout()
        anim_layout.setContentsMargins(15, 10, 15, 10)
        
        anim_label = QLabel('🎬 Animações')
        anim_label.setStyleSheet('font-size: 13px; color: #2d2d2d;')
        anim_layout.addWidget(anim_label, stretch=1)
        
        anim_toggle = QCheckBox()
        anim_toggle.setChecked(True)  # Por padrão ativado
        anim_toggle.setStyleSheet("""
            QCheckBox::indicator {
                width: 40px;
                height: 20px;
                border-radius: 10px;
                background-color: #88c22b;
            }
            QCheckBox::indicator:unchecked {
                background-color: #ccc;
            }
        """)
        anim_layout.addWidget(anim_toggle)
        
        anim_container.setLayout(anim_layout)
        self.content_layout.addWidget(anim_container)

        # Botão de gerenciar usuários (só para admin)
        if self.user_data.get('is_admin'):
            self.content_layout.addSpacing(10)
            
            btn_manage_users = QPushButton('👥 Gerenciar Usuários Pendentes')
            btn_manage_users.setStyleSheet("""
                QPushButton {
                    background-color: #406e54;
                    color: white;
                    border: none;
                    padding: 12px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #355a45;
                }
            """)
            btn_manage_users.clicked.connect(self.open_manage_users)
            self.content_layout.addWidget(btn_manage_users)

        self.content_layout.addStretch()
        
        # Botão logout
        btn_logout = QPushButton('🚪 Sair da Conta')
        btn_logout.setStyleSheet("""
            QPushButton {
                background-color: #82414c;
                color: white;
                border: none;
                padding: 12px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6d363f;
            }
        """)
        btn_logout.clicked.connect(self.do_logout)
        self.content_layout.addWidget(btn_logout)

    def do_logout(self):
        """Fazer logout e voltar pra tela de login"""
        msg = QMessageBox(self)
        msg.setWindowTitle('Confirmar Logout')
        msg.setText('Deseja realmente sair da sua conta?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        result = msg.exec()
        
        if msg.clickedButton() == btn_sim:
            # Deslogar do Firebase
            self.firebase.logout()
            
            # Fechar tudo e voltar pro login
            self.close()
            if self.circle_parent:
                self.circle_parent.close()
            
            # Reabrir tela de login
            QApplication.instance().quit()

    def open_manage_users(self):
        """Abrir janela de gerenciar usuários"""
        self.manage_window = ManageUsersWindow(self.firebase)
        self.manage_window.show()

    def on_shortcut_selection_changed(self, shortcut_id, state):
        """Callback quando checkbox de atalho é alterado"""
        if state == Qt.CheckState.Checked.value:
            if shortcut_id not in self.selected_shortcuts:
                self.selected_shortcuts.append(shortcut_id)
        else:
            if shortcut_id in self.selected_shortcuts:
                self.selected_shortcuts.remove(shortcut_id)
        
        # Mostrar/ocultar botão de deletar selecionados
        if len(self.selected_shortcuts) > 0:
            self.btn_delete_selected_shortcuts.show()
        else:
            self.btn_delete_selected_shortcuts.hide()
    
    def delete_selected_shortcuts(self):
        """Deletar atalhos selecionados"""
        if not self.selected_shortcuts:
            return
        
        msg = QMessageBox(self)
        msg.setWindowTitle('Confirmar exclusão')
        msg.setText(f'Deseja realmente excluir {len(self.selected_shortcuts)} atalho(s)?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        msg.setStyleSheet("""
            QMessageBox {
                background-color: white;
            }
            QMessageBox QLabel {
                color: #2d2d2d;
                font-size: 13px;
            }
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            for shortcut_id in self.selected_shortcuts:
                self.db.delete_shortcut(shortcut_id)
            self.selected_shortcuts.clear()
            self.show_atalhos_tab()
    
    def add_shortcut(self):
        print("MainMenu: Abrindo janela de adicionar atalho")
        if self.add_window and self.add_window.isVisible():
            self.add_window.close()
        
        self.add_window = AddShortcutWindow(self.db, menu_ref=self)
        self.add_window.show()
        self.add_window.raise_()
        self.add_window.activateWindow()
    
    def edit_shortcut(self, shortcut):
        print(f"MainMenu: Editando atalho {shortcut['id']}")
        if self.add_window and self.add_window.isVisible():
            self.add_window.close()
        
        self.add_window = AddShortcutWindow(self.db, menu_ref=self, shortcut_data=shortcut)
        self.add_window.show()
        self.add_window.raise_()
        self.add_window.activateWindow()
    
    def delete_shortcut(self, shortcut_id):
        msg = QMessageBox()
        msg.setWindowTitle('Confirmar exclusão')
        msg.setText('Deseja realmente excluir este atalho?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        msg.setStyleSheet("""
            QMessageBox {
                background-color: white;
            }
            QMessageBox QLabel {
                color: #2d2d2d;
                font-size: 13px;
            }
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        
        result = msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.db.delete_shortcut(shortcut_id)
            self.show_atalhos_tab()
        
        # Reabrir menu
        self.show()
        self.raise_()
        self.activateWindow()
    
    def toggle_shortcut_status(self, shortcut_id):
        self.db.toggle_shortcut(shortcut_id)
        # Recarregar listener para refletir mudanças de conflito
        if hasattr(self, 'circle_parent') and self.circle_parent:
            QTimer.singleShot(100, lambda: None)  # Dar tempo ao banco
        self.show_atalhos_tab()
    
    def add_template(self):
        print("MainMenu: Abrindo janela de adicionar template")
        try:
            # Fechar janela anterior se existir
            if self.add_window and self.add_window.isVisible():
                print("MainMenu: Fechando janela anterior")
                self.add_window.close()
            
            # Criar nova janela
            self.add_window = AddTemplateWindow(self.db, menu_ref=self)
            print(f"MainMenu: Janela criada: {self.add_window}")
            print(f"MainMenu: Parent da janela: {self.add_window.parent()}")
            
            # Importante: NÃO fechar o menu
            # self.close()  <- REMOVIDO
            
            self.add_window.show()
            self.add_window.raise_()
            self.add_window.activateWindow()
            print("MainMenu: Janela mostrada com sucesso")
        except Exception as e:
            print(f"MainMenu: ERRO ao criar janela: {e}")
            import traceback
            traceback.print_exc()
    
    def sair_programa(self):
        msg = QMessageBox(self)
        msg.setWindowTitle('Confirmar saída')
        msg.setText('Deseja realmente fechar o programa?')
        
        # Customizar botões em português
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        msg.setStyleSheet("""
            QMessageBox {
                background-color: white;
            }
            QLabel {
                color: #2d2d2d;
                font-size: 13px;
            }
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            QApplication.quit()
    
    def focusOutEvent(self, event):
        # Com Popup, não precisa fazer nada aqui
        pass
    
    def closeEvent(self, event):
        print("MainMenu: closeEvent")
        event.accept()


class EditableActionsList(QWidget):
    """Widget de lista de ações com edição, exclusão e reordenação por drag-and-drop"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.acoes = []
        self.action_widgets = []
        self.dragged_widget = None
        self.drag_start_index = -1
        self.placeholder_widget = None
        self.placeholder_index = -1
        
        # Estado de reorganização visual durante drag
        self.is_dragging = False
        self.drag_from_index = -1
        self.drag_hover_index = -1
        
        self.init_ui()
    
    def init_ui(self):
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(5)
        
        # Scroll area para as ações
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
        """)
        
        self.actions_container = QWidget()
        self.actions_container.setAcceptDrops(True)  # Habilitar drops no container
        self.actions_layout = QVBoxLayout()
        self.actions_layout.setContentsMargins(5, 5, 5, 5)
        self.actions_layout.setSpacing(5)
        self.actions_container.setLayout(self.actions_layout)
        
        scroll.setWidget(self.actions_container)
        
        self.main_layout.addWidget(scroll)
        self.setLayout(self.main_layout)
        
        self.setMinimumHeight(200)
    
    def add_acao(self, acao):
        """Adicionar nova ação"""
        self.acoes.append(acao)
        self.refresh_list()
    
    def set_acoes(self, acoes):
        """Definir lista completa de ações"""
        self.acoes = acoes.copy()
        self.refresh_list()
    
    def get_acoes(self):
        """Retornar lista de ações"""
        return self.acoes.copy()
    
    def refresh_list(self):
        """Atualizar visualização da lista"""
        # Limpar widgets anteriores
        while self.actions_layout.count():
            child = self.actions_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.action_widgets.clear()
        
        # Criar widget para cada ação
        for i, acao in enumerate(self.acoes):
            action_widget = ActionItemWidget(i, acao, self)
            self.actions_layout.addWidget(action_widget)
            self.action_widgets.append(action_widget)
        
        self.actions_layout.addStretch()
    
    def edit_acao(self, index):
        """Editar ação no índice especificado"""
        if 0 <= index < len(self.acoes):
            acao = self.acoes[index]
            
            if acao['type'] == 'type':
                self.edit_type_action(index, acao)
            elif acao['type'] == 'click' or acao['type'] == 'right_click':
                self.edit_click_action(index, acao)
            elif acao['type'] == 'drag':
                self.edit_drag_action(index, acao)
            elif acao['type'] == 'sleep':
                self.edit_sleep_action(index, acao)
    
    def edit_type_action(self, index, acao):
        """Editar ação de digitar"""
        from PyQt6.QtWidgets import QInputDialog
        texto, ok = QInputDialog.getText(
            self, 
            'Editar Texto', 
            'Texto para digitar:',
            text=acao['text']
        )
        if ok and texto:
            self.acoes[index]['text'] = texto
            self.refresh_list()
    
    def edit_click_action(self, index, acao):
        """Editar ação de clique"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox, QRadioButton, QButtonGroup
        
        dialog = QDialog()  # Sem parent para não fechar com a janela pai
        dialog.setWindowTitle('Editar Clique')
        dialog.setFixedWidth(300)
        dialog.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint
        )
        
        layout = QVBoxLayout()
        
        # Tipo de botão
        layout.addWidget(QLabel('Tipo de botão:'))
        
        btn_group = QButtonGroup(dialog)
        radio_esquerdo = QRadioButton('Botão Esquerdo')
        radio_direito = QRadioButton('Botão Direito')
        btn_group.addButton(radio_esquerdo)
        btn_group.addButton(radio_direito)
        
        if acao['type'] == 'right_click':
            radio_direito.setChecked(True)
        else:
            radio_esquerdo.setChecked(True)
        
        layout.addWidget(radio_esquerdo)
        layout.addWidget(radio_direito)
        
        layout.addWidget(QLabel('\nQuantos cliques?'))
        spin_vezes = QSpinBox()
        spin_vezes.setMinimum(1)
        spin_vezes.setMaximum(100)
        spin_vezes.setValue(acao.get('vezes', 1))
        layout.addWidget(spin_vezes)
        
        label_pos = QLabel(f'\nPosição atual: ({acao["x"]}, {acao["y"]})')
        layout.addWidget(label_pos)
        
        spin_x = QSpinBox()
        spin_x.setMinimum(0)
        spin_x.setMaximum(10000)
        spin_x.setValue(acao['x'])
        layout.addWidget(spin_x)
        spin_x.setVisible(False)
        
        spin_y = QSpinBox()
        spin_y.setMinimum(0)
        spin_y.setMaximum(10000)
        spin_y.setValue(acao['y'])
        layout.addWidget(spin_y)
        spin_y.setVisible(False)
        
        def update_label():
            label_pos.setText(f'\nPosição atual: ({spin_x.value()}, {spin_y.value()})')
        
        spin_x.valueChanged.connect(update_label)
        spin_y.valueChanged.connect(update_label)
        
        btn_recapture = QPushButton('📍 Recapturar Posição')
        btn_recapture.setStyleSheet("""
            QPushButton { background-color: #406e54; color: white; padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #355a45; }
        """)
        btn_recapture.clicked.connect(lambda: self.recapture_position(dialog, spin_x, spin_y))
        layout.addWidget(btn_recapture)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        dialog.setLayout(layout)
        
        # Usar accepted/rejected em vez de exec()
        def on_accepted():
            print(f"DEBUG salvando: spin_x={spin_x.value()}, spin_y={spin_y.value()}")
            if radio_direito.isChecked():
                self.acoes[index]['type'] = 'right_click'
                if 'vezes' in self.acoes[index]:
                    del self.acoes[index]['vezes']
            else:
                self.acoes[index]['type'] = 'click'
                self.acoes[index]['vezes'] = spin_vezes.value()
            self.acoes[index]['x'] = spin_x.value()
            self.acoes[index]['y'] = spin_y.value()
            print(f"DEBUG acao salva: {self.acoes[index]}")
            self.refresh_list()
        
        buttons.accepted.connect(on_accepted)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        
        dialog.exec()
    
    def edit_right_click_action(self, index, acao):
        """Editar ação de clique direito"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle('Editar Clique Direito')
        dialog.setFixedWidth(300)
        
        layout = QVBoxLayout()
        
        # Posição atual (label que será atualizado)
        label_pos = QLabel(f'Posição atual: ({acao["x"]}, {acao["y"]})')
        layout.addWidget(label_pos)
        
        # Spinboxes adicionados ao layout para garantir referência (mas invisíveis)
        spin_x = QSpinBox()
        spin_x.setMinimum(0)
        spin_x.setMaximum(10000)
        spin_x.setValue(acao['x'])
        layout.addWidget(spin_x)
        spin_x.setVisible(False)
        
        spin_y = QSpinBox()
        spin_y.setMinimum(0)
        spin_y.setMaximum(10000)
        spin_y.setValue(acao['y'])
        layout.addWidget(spin_y)
        spin_y.setVisible(False)
        
        # Função para atualizar label
        def update_label():
            label_pos.setText(f'Posição atual: ({spin_x.value()}, {spin_y.value()})')
        
        spin_x.valueChanged.connect(update_label)
        spin_y.valueChanged.connect(update_label)
        
        # Botão para recapturar posição
        btn_recapture = QPushButton('📍 Recapturar Posição')
        btn_recapture.setStyleSheet("""
            QPushButton {
                background-color: #406e54;
                color: white;
                padding: 8px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #355a45;
            }
        """)
        btn_recapture.clicked.connect(lambda: self.recapture_position(dialog, spin_x, spin_y))
        layout.addWidget(btn_recapture)
        
        # Botões
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.acoes[index]['x'] = spin_x.value()
            self.acoes[index]['y'] = spin_y.value()
            self.refresh_list()
    
    def edit_drag_action(self, index, acao):
        """Editar ação de arrastar"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox
        
        dialog = QDialog()  # Sem parent
        dialog.setWindowTitle('Editar Arraste')
        dialog.setFixedWidth(320)
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout()
        
        label_inicio = QLabel(f'Início: ({acao["x1"]}, {acao["y1"]})')
        layout.addWidget(label_inicio)
        
        label_fim = QLabel(f'Fim: ({acao["x2"]}, {acao["y2"]})')
        layout.addWidget(label_fim)
        
        spin_x1 = QSpinBox()
        spin_x1.setMinimum(0)
        spin_x1.setMaximum(10000)
        spin_x1.setValue(acao['x1'])
        layout.addWidget(spin_x1)
        spin_x1.setVisible(False)
        
        spin_y1 = QSpinBox()
        spin_y1.setMinimum(0)
        spin_y1.setMaximum(10000)
        spin_y1.setValue(acao['y1'])
        layout.addWidget(spin_y1)
        spin_y1.setVisible(False)
        
        spin_x2 = QSpinBox()
        spin_x2.setMinimum(0)
        spin_x2.setMaximum(10000)
        spin_x2.setValue(acao['x2'])
        layout.addWidget(spin_x2)
        spin_x2.setVisible(False)
        
        spin_y2 = QSpinBox()
        spin_y2.setMinimum(0)
        spin_y2.setMaximum(10000)
        spin_y2.setValue(acao['y2'])
        layout.addWidget(spin_y2)
        spin_y2.setVisible(False)
        
        def update_labels():
            label_inicio.setText(f'Início: ({spin_x1.value()}, {spin_y1.value()})')
            label_fim.setText(f'Fim: ({spin_x2.value()}, {spin_y2.value()})')
        
        spin_x1.valueChanged.connect(update_labels)
        spin_y1.valueChanged.connect(update_labels)
        spin_x2.valueChanged.connect(update_labels)
        spin_y2.valueChanged.connect(update_labels)
        
        btn_recapture = QPushButton('📍 Recapturar Arraste')
        btn_recapture.setStyleSheet("""
            QPushButton { background-color: #406e54; color: white; padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #355a45; }
        """)
        btn_recapture.clicked.connect(lambda: self.recapture_drag(dialog, spin_x1, spin_y1, spin_x2, spin_y2))
        layout.addWidget(btn_recapture)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        dialog.setLayout(layout)
        
        def on_accepted():
            self.acoes[index]['x1'] = spin_x1.value()
            self.acoes[index]['y1'] = spin_y1.value()
            self.acoes[index]['x2'] = spin_x2.value()
            self.acoes[index]['y2'] = spin_y2.value()
            self.refresh_list()
        
        buttons.accepted.connect(on_accepted)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        
        dialog.exec()
    
    def edit_sleep_action(self, index, acao):
        """Editar ação de espera"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle('Editar Espera')
        dialog.setFixedWidth(300)
        
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel('Tempo em milissegundos:'))
        
        spinbox = QSpinBox()
        spinbox.setMinimum(100)
        spinbox.setMaximum(60000)
        spinbox.setValue(acao['ms'])
        spinbox.setSingleStep(100)
        layout.addWidget(spinbox)
        
        # Label de descrição
        desc_label = QLabel('')
        desc_label.setStyleSheet('color: #666; font-size: 11px; font-style: italic;')
        layout.addWidget(desc_label)
        
        # Atualizar descrição quando valor muda
        def update_desc(value):
            segundos = value / 1000.0
            if segundos == 1.0:
                desc_label.setText('1000ms = 1 segundo')
            else:
                desc_label.setText(f'{value}ms = {segundos:.1f} segundos')
        
        spinbox.valueChanged.connect(update_desc)
        update_desc(acao['ms'])
        
        # Botões
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.acoes[index]['ms'] = spinbox.value()
            self.refresh_list()
    
    def recapture_position(self, dialog, spin_x, spin_y):
        """Recapturar posição do mouse"""
        dialog.hide()
        
        self.recapture_overlay = ClickCaptureOverlay()
        self.recapture_overlay.coordinate_captured.connect(lambda x, y: self.on_recapture_position(dialog, spin_x, spin_y, x, y))
        self.recapture_overlay.showFullScreen()
    
    def on_recapture_position(self, dialog, spin_x, spin_y, x, y):
        """Callback após recapturar posição"""
        print(f"DEBUG on_recapture_position: x={x}, y={y}")
        spin_x.setValue(int(x))
        spin_y.setValue(int(y))
        print(f"DEBUG spin depois: {spin_x.value()}, {spin_y.value()}")
        
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.recapture_overlay = None
    
    def recapture_drag(self, dialog, spin_x1, spin_y1, spin_x2, spin_y2):
        """Recapturar arraste"""
        dialog.hide()
        
        self.recapture_overlay = DragCaptureOverlay()
        self.recapture_overlay.drag_captured.connect(lambda x1, y1, x2, y2: self.on_recapture_drag(dialog, spin_x1, spin_y1, spin_x2, spin_y2, x1, y1, x2, y2))
        self.recapture_overlay.showFullScreen()
    
    def on_recapture_drag(self, dialog, spin_x1, spin_y1, spin_x2, spin_y2, x1, y1, x2, y2):
        """Callback após recapturar arraste"""
        spin_x1.setValue(x1)
        spin_y1.setValue(y1)
        spin_x2.setValue(x2)
        spin_y2.setValue(y2)
        
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.recapture_overlay = None
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    
    def delete_acao(self, index):
        """Deletar ação"""
        if 0 <= index < len(self.acoes):
            del self.acoes[index]
            self.refresh_list()
    
    def move_acao(self, from_index, to_index):
        """Mover ação de uma posição para outra"""
        if not (0 <= from_index < len(self.acoes)):
            return
        
        if not (0 <= to_index < len(self.acoes)):
            return
        
        if from_index == to_index:
            return
        
        # Remover item da posição original
        acao = self.acoes.pop(from_index)
        
        # Inserir na nova posição
        self.acoes.insert(to_index, acao)
        
        # Atualizar visualização
        self.refresh_list()
    
    def create_placeholder(self):
        """Criar widget placeholder para mostrar onde o item será solto"""
        placeholder = QWidget()
        placeholder.setStyleSheet("""
            QWidget {
                background-color: rgba(136, 194, 43, 0.3);
                border: 2px dashed #88c22b;
                border-radius: 6px;
            }
        """)
        placeholder.setMinimumHeight(45)
        return placeholder
    
    def show_placeholder_at(self, index):
        """Mostrar placeholder na posição especificada"""
        # Remover placeholder anterior se existir
        if self.placeholder_widget:
            self.actions_layout.removeWidget(self.placeholder_widget)
            self.placeholder_widget.deleteLater()
            self.placeholder_widget = None
        
        # Criar novo placeholder
        if 0 <= index <= len(self.action_widgets):
            self.placeholder_widget = self.create_placeholder()
            self.actions_layout.insertWidget(index, self.placeholder_widget)
            self.placeholder_index = index
    
    def remove_placeholder(self):
        """Remover placeholder"""
        if self.placeholder_widget:
            self.actions_layout.removeWidget(self.placeholder_widget)
            self.placeholder_widget.deleteLater()
            self.placeholder_widget = None
            self.placeholder_index = -1
    
    def reorder_visual_for_drag(self, from_index, hover_index):
        """Reorganizar visualmente os widgets enquanto arrasta"""
        if from_index == hover_index or hover_index == -1:
            return
        
        # Criar lista temporária da ordem visual desejada
        temp_order = list(range(len(self.action_widgets)))
        
        # Remover item da posição original
        item = temp_order.pop(from_index)
        
        # Inserir na posição de hover
        temp_order.insert(hover_index, item)
        
        # Reorganizar widgets no layout seguindo a nova ordem
        for visual_pos, actual_index in enumerate(temp_order):
            widget = self.action_widgets[actual_index]
            # Remove e reinsere na nova posição visual
            self.actions_layout.removeWidget(widget)
            self.actions_layout.insertWidget(visual_pos, widget)
    
    def restore_visual_order(self):
        """Restaurar ordem visual original dos widgets"""
        for i, widget in enumerate(self.action_widgets):
            self.actions_layout.removeWidget(widget)
            self.actions_layout.insertWidget(i, widget)
    
    def cancel_drag(self):
        """Cancelar drag e restaurar tudo ao estado original"""
        self.is_dragging = False
        self.drag_from_index = -1
        self.drag_hover_index = -1
        self.restore_visual_order()


class ActionItemWidget(QWidget):
    """Widget individual para cada ação na lista"""
    
    def __init__(self, index, acao, parent_list):
        super().__init__()
        self.index = index
        self.acao = acao
        self.parent_list = parent_list
        
        self.init_ui()
    
    def init_ui(self):
        self.setStyleSheet("""
            ActionItemWidget {
                background-color: #ffffff;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
            }
            ActionItemWidget:hover {
                background-color: #f8f9fa;
                border: 2px solid #88c22b;
            }
        """)
        
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        
        # Botões de mover para cima/baixo
        move_layout = QVBoxLayout()
        move_layout.setSpacing(0)
        move_layout.setContentsMargins(0, 0, 5, 0)
        
        btn_up = QPushButton('▲')
        btn_up.setFixedSize(20, 20)
        btn_up.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #ddd;
                border: none;
                font-size: 10px;
                padding: 0px;
            }
            QPushButton:hover:enabled {
                color: #88c22b;
                background-color: rgba(136, 194, 43, 0.1);
            }
            QPushButton:disabled {
                color: #333;
            }
        """)
        btn_up.clicked.connect(self.move_up)
        if self.index == 0:
            btn_up.setEnabled(False)
        move_layout.addWidget(btn_up)
        
        btn_down = QPushButton('▼')
        btn_down.setFixedSize(20, 20)
        btn_down.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #ddd;
                border: none;
                font-size: 10px;
                padding: 0px;
            }
            QPushButton:hover:enabled {
                color: #88c22b;
                background-color: rgba(136, 194, 43, 0.1);
            }
            QPushButton:disabled {
                color: #333;
            }
        """)
        btn_down.clicked.connect(self.move_down)
        if self.index == len(self.parent_list.acoes) - 1:
            btn_down.setEnabled(False)
        move_layout.addWidget(btn_down)
        
        layout.addLayout(move_layout)
        
        # Layout vertical: texto + observação
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)
        
        # Texto da ação - para sleep, usar campo editável inline
        if self.acao['type'] == 'sleep':
            sleep_layout = QHBoxLayout()
            sleep_layout.setSpacing(0)
            sleep_layout.setContentsMargins(0, 0, 0, 0)
            
            numero_label = QLabel(f"{self.index + 1}. Esperar ")
            numero_label.setStyleSheet('color: #e0e0e0; font-size: 13px; font-weight: 500;')
            sleep_layout.addWidget(numero_label, 0)
            
            self.ms_input = QLineEdit(str(self.acao['ms']))
            self.ms_input.setFixedWidth(60)
            self.ms_input.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.ms_input.setStyleSheet("""
                QLineEdit {
                    color: #e0e0e0;
                    font-size: 13px;
                    font-weight: 500;
                    background-color: transparent;
                    border: none;
                    border-bottom: 1px solid transparent;
                    padding: 0px;
                    margin: 0px;
                }
                QLineEdit:focus {
                    border-bottom: 1px solid #88c22b;
                    background-color: rgba(136, 194, 43, 0.1);
                }
            """)
            self.ms_input.setReadOnly(True)
            self.ms_input.mouseDoubleClickEvent = lambda e: self.enable_edit()
            self.ms_input.returnPressed.connect(self.save_sleep_edit)
            self.ms_input.editingFinished.connect(self.save_sleep_edit)
            sleep_layout.addWidget(self.ms_input, 0)
            
            segundos = self.acao['ms'] / 1000.0
            self.suffix_label = QLabel(f"ms ({segundos:.1f}s)")
            self.suffix_label.setStyleSheet('color: #e0e0e0; font-size: 13px; font-weight: 500; margin-left: 0px; padding-left: 0px;')
            sleep_layout.addWidget(self.suffix_label, 0)
            sleep_layout.addStretch(1)
            text_col.addLayout(sleep_layout)
        else:
            texto = self.get_action_text()
            self.label = QLabel(texto)
            self.label.setStyleSheet('color: #e0e0e0; font-size: 13px; font-weight: 500;')
            text_col.addWidget(self.label)
        
        # Campo de observação (editável com duplo clique)
        obs_texto = self.acao.get('obs', '')
        self.obs_input = QLineEdit(obs_texto)
        self.obs_input.setPlaceholderText('Duplo clique para adicionar observação...')
        self.obs_input.setStyleSheet("""
            QLineEdit {
                color: #aaa;
                font-size: 11px;
                font-style: italic;
                background-color: transparent;
                border: none;
                border-bottom: 1px solid transparent;
                padding: 0px;
                margin: 0px;
            }
            QLineEdit:focus {
                color: #ccc;
                border-bottom: 1px solid #88c22b;
                background-color: rgba(136, 194, 43, 0.05);
            }
        """)
        self.obs_input.setReadOnly(True)
        self.obs_input.mouseDoubleClickEvent = lambda e: self.enable_obs_edit()
        self.obs_input.returnPressed.connect(self.save_obs)
        self.obs_input.editingFinished.connect(self.save_obs)
        # Mostrar campo se já tem texto
        if not obs_texto:
            self.obs_input.hide()
        text_col.addWidget(self.obs_input)
        
        layout.addLayout(text_col, stretch=1)
        
        # Botão de observação 💬 (sempre visível, antes de editar/deletar)
        self.btn_obs = QPushButton('💬')
        self.btn_obs.setFixedSize(24, 24)
        obs_atual = self.acao.get('obs', '')
        self.btn_obs.setToolTip(obs_atual if obs_atual else 'Adicionar observação')
        self.btn_obs.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {'#88c22b' if obs_atual else '#555'};
                border: none;
                border-radius: 4px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: rgba(136, 194, 43, 0.15);
                color: #88c22b;
            }}
        """)
        self.btn_obs.clicked.connect(self.on_obs_clicked)
        self.btn_obs.hide()
        layout.addWidget(self.btn_obs)
        
        # Botões de ação (inicialmente ocultos)
        self.btn_edit = QPushButton('✎')
        self.btn_edit.setFixedSize(24, 24)
        self.btn_edit.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #406e54;
                border: none;
                border-radius: 4px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: rgba(64, 110, 84, 0.15);
            }
        """)
        self.btn_edit.clicked.connect(self.on_edit_clicked)
        self.btn_edit.hide()
        layout.addWidget(self.btn_edit)
        
        self.btn_delete = QPushButton('🗑')
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #82414c;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(130, 65, 76, 0.15);
            }
        """)
        self.btn_delete.clicked.connect(self.on_delete_clicked)
        self.btn_delete.hide()
        layout.addWidget(self.btn_delete)
        
        self.setLayout(layout)
        self.setMinimumHeight(45)
    
    def enable_obs_edit(self):
        """Habilitar edição da observação"""
        self.obs_input.setReadOnly(False)
        self.obs_input.show()
        self.obs_input.setFocus()
        self.obs_input.selectAll()
    
    def save_obs(self):
        """Salvar observação"""
        texto = self.obs_input.text().strip()
        self.parent_list.acoes[self.index]['obs'] = texto
        self.obs_input.setReadOnly(True)
        if not texto:
            self.obs_input.hide()
        else:
            self.obs_input.show()
    
    def get_action_text(self):
        """Obter texto descritivo da ação"""
        acao = self.acao
        i = self.index
        
        if acao['type'] == 'click':
            vezes = acao.get('vezes', 1)
            if vezes == 1:
                return f"{i+1}. Clique em ({acao['x']}, {acao['y']})"
            else:
                return f"{i+1}. Clique {vezes}x em ({acao['x']}, {acao['y']})"
        elif acao['type'] == 'right_click':
            return f"{i+1}. Clique direito em ({acao['x']}, {acao['y']})"
        elif acao['type'] == 'drag':
            return f"{i+1}. Arrastar de ({acao['x1']}, {acao['y1']}) até ({acao['x2']}, {acao['y2']})"
        elif acao['type'] == 'sleep':
            segundos = acao['ms'] / 1000.0
            return f"{i+1}. Esperar {acao['ms']}ms ({segundos:.1f}s)"
        elif acao['type'] == 'type':
            preview = acao['text'][:30] + '...' if len(acao['text']) > 30 else acao['text']
            return f"{i+1}. Digitar: {preview}"
        return f"{i+1}. Ação desconhecida"
    
    def enterEvent(self, event):
        """Mostrar botões ao passar mouse"""
        self.btn_obs.show()
        self.btn_edit.show()
        self.btn_delete.show()
    
    def leaveEvent(self, event):
        """Ocultar botões ao sair mouse"""
        self.btn_obs.hide()
        self.btn_edit.hide()
        self.btn_delete.hide()
    
    def on_edit_clicked(self):
        """Callback botão editar"""
        self.parent_list.edit_acao(self.index)
    
    def on_obs_clicked(self):
        """Mostrar campo de observação inline para edição"""
        self.obs_input.show()
        self.obs_input.setReadOnly(False)
        self.obs_input.setFocus()
        self.obs_input.selectAll()
    
    def enable_obs_edit(self):
        """Habilitar edição da observação com duplo clique"""
        self.obs_input.setReadOnly(False)
        self.obs_input.setFocus()
        self.obs_input.selectAll()
    
    def save_obs(self):
        """Salvar observação"""
        obs = self.obs_input.text().strip()
        self.parent_list.acoes[self.index]['obs'] = obs
        self.obs_input.setReadOnly(True)
        if not obs:
            self.obs_input.hide()
        else:
            self.obs_input.show()
        # Atualizar ícone do botão
        self.btn_obs.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {'#88c22b' if obs else '#555'};
                border: none;
                border-radius: 4px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: rgba(136, 194, 43, 0.15);
                color: #88c22b;
            }}
        """)
    
    def on_delete_clicked(self):
        """Callback botão deletar"""
        msg = QMessageBox()
        msg.setWindowTitle('Confirmar exclusão')
        msg.setText('Deseja realmente excluir esta ação?')
        
        btn_sim = msg.addButton('Sim', QMessageBox.ButtonRole.YesRole)
        btn_nao = msg.addButton('Não', QMessageBox.ButtonRole.NoRole)
        
        result = msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.parent_list.delete_acao(self.index)
        
        # Reabrir janela de atalho se existir
        if self.parent_list.parent_window:
            self.parent_list.parent_window.show()
            self.parent_list.parent_window.raise_()
            self.parent_list.parent_window.activateWindow()
    
    def move_up(self):
        """Mover ação para cima (uma posição)"""
        if self.index > 0:
            self.parent_list.move_acao(self.index, self.index - 1)
    
    def move_down(self):
        """Mover ação para baixo (uma posição)"""
        if self.index < len(self.parent_list.acoes) - 1:
            self.parent_list.move_acao(self.index, self.index + 1)
    
    def enable_edit(self):
        """Habilitar edição do campo de ms (duplo clique)"""
        if hasattr(self, 'ms_input'):
            self.ms_input.setReadOnly(False)
            self.ms_input.setFocus()
            self.ms_input.selectAll()
    
    def save_sleep_edit(self):
        """Salvar novo valor de ms editado"""
        if hasattr(self, 'ms_input'):
            try:
                novo_ms = int(self.ms_input.text())
                if 100 <= novo_ms <= 60000:  # Validar range
                    self.parent_list.acoes[self.index]['ms'] = novo_ms
                    # Atualizar sufixo
                    segundos = novo_ms / 1000.0
                    self.suffix_label.setText(f"ms ({segundos:.1f}s)")
                    self.ms_input.setReadOnly(True)
                else:
                    # Valor inválido - restaurar original
                    self.ms_input.setText(str(self.parent_list.acoes[self.index]['ms']))
                    self.ms_input.setReadOnly(True)
            except ValueError:
                # Não é número - restaurar original
                self.ms_input.setText(str(self.parent_list.acoes[self.index]['ms']))
                self.ms_input.setReadOnly(True)


class AddShortcutWindow(QWidget):
    def __init__(self, db, menu_ref=None, shortcut_data=None):
        print("AddShortcutWindow: __init__ chamado")
        super().__init__(None)
        
        self.db = db
        self.menu_reference = menu_ref
        self.shortcut_data = shortcut_data  # Para edição
        self.acoes = shortcut_data['acoes'] if shortcut_data else []
        self.shortcut_id = shortcut_data['id'] if shortcut_data else None
        
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        self.init_ui()
    
    def init_ui(self):
        titulo = 'Editar Atalho' if self.shortcut_data else 'Novo Atalho'
        self.setWindowTitle(titulo)
        self.setFixedSize(550, 600)
        
        layout = QVBoxLayout()
        
        title = QLabel(titulo)
        title.setStyleSheet('font-weight: bold; font-size: 15px; padding: 10px;')
        layout.addWidget(title)
        
        layout.addWidget(QLabel('Nome do atalho:'))
        self.nome_input = QLineEdit()
        self.nome_input.setPlaceholderText('Ex: Abrir planilha')
        if self.shortcut_data:
            self.nome_input.setText(self.shortcut_data['nome'])
        layout.addWidget(self.nome_input)
        
        # Tipo de ativação
        layout.addWidget(QLabel('Como ativar:'))
        tipo_layout = QHBoxLayout()
        
        self.tipo_combo = QComboBox()
        self.tipo_combo.addItems(['Alt + Tecla', 'Atalho de texto (ex: bd + espaço)'])
        self.tipo_combo.currentIndexChanged.connect(self.on_tipo_changed)
        tipo_layout.addWidget(self.tipo_combo)
        
        self.atalho_input = QLineEdit()
        self.atalho_input.setPlaceholderText('Digite a tecla (ex: C para Alt+C)')
        
        # Detectar tipo baseado em dado existente
        if self.shortcut_data and self.shortcut_data.get('tecla_atalho'):
            tecla = self.shortcut_data['tecla_atalho']
            if len(tecla) <= 2 and not '+' in tecla:
                # É Alt + tecla
                self.tipo_combo.setCurrentIndex(0)
                self.atalho_input.setText(tecla)
            else:
                # É atalho de texto
                self.tipo_combo.setCurrentIndex(1)
                self.atalho_input.setText(tecla)
        
        tipo_layout.addWidget(self.atalho_input)
        
        layout.addLayout(tipo_layout)
        
        layout.addWidget(QLabel('Ações:'))
        
        # Lista de ações customizada com drag-and-drop
        self.acoes_list = EditableActionsList(self)
        layout.addWidget(self.acoes_list)
        
        # Atualizar lista se estiver editando
        if self.acoes:
            self.acoes_list.set_acoes(self.acoes)
        
        # Botões para adicionar ações
        acoes_buttons = QHBoxLayout()
        
        btn_click = QPushButton('+ Clique')
        btn_click.clicked.connect(self.add_click_action)
        acoes_buttons.addWidget(btn_click)
        
        btn_drag = QPushButton('+ Arrastar')
        btn_drag.clicked.connect(self.add_drag_action)
        acoes_buttons.addWidget(btn_drag)
        
        layout.addLayout(acoes_buttons)
        
        # Segunda linha de botões
        acoes_buttons2 = QHBoxLayout()
        
        btn_type = QPushButton('+ Digitar')
        btn_type.clicked.connect(self.add_type_action)
        acoes_buttons2.addWidget(btn_type)
        
        btn_sleep = QPushButton('+ Esperar')
        btn_sleep.clicked.connect(self.add_sleep_action)
        acoes_buttons2.addWidget(btn_sleep)
        
        layout.addLayout(acoes_buttons2)
        
        # Botões finais
        btn_layout = QHBoxLayout()
        
        btn_salvar = QPushButton('✓ Salvar')
        btn_salvar.setStyleSheet("""
            QPushButton {
                background-color: #88c22b;
                color: white;
                padding: 10px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #79b325;
            }
        """)
        btn_salvar.clicked.connect(self.salvar)
        btn_layout.addWidget(btn_salvar)
        
        btn_cancelar = QPushButton('✗ Cancelar')
        btn_cancelar.clicked.connect(self.close)
        btn_layout.addWidget(btn_cancelar)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def on_tipo_changed(self, index):
        if index == 0:
            self.atalho_input.setPlaceholderText('Digite a tecla (ex: C para Alt+C)')
        else:
            self.atalho_input.setPlaceholderText('Digite o atalho (ex: bd, otb)')
    
    def add_type_action(self):
        from PyQt6.QtWidgets import QInputDialog
        texto, ok = QInputDialog.getText(self, 'Digitar texto', 'Texto para digitar:')
        if ok and texto:
            self.acoes_list.add_acao({'type': 'type', 'text': texto})
    
    def add_click_action(self):
        # Dialog personalizado para escolher tipo e quantidade
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QRadioButton, QButtonGroup, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle('Adicionar Clique')
        dialog.setFixedWidth(300)
        
        layout = QVBoxLayout()
        
        # Tipo de botão
        layout.addWidget(QLabel('Tipo de botão:'))
        
        btn_group = QButtonGroup(dialog)
        radio_esquerdo = QRadioButton('Botão Esquerdo')
        radio_direito = QRadioButton('Botão Direito')
        
        btn_group.addButton(radio_esquerdo)
        btn_group.addButton(radio_direito)
        
        # Esquerdo selecionado por padrão
        radio_esquerdo.setChecked(True)
        
        layout.addWidget(radio_esquerdo)
        layout.addWidget(radio_direito)
        
        # Quantidade de cliques
        layout.addWidget(QLabel('\nQuantos cliques?'))
        spin_vezes = QSpinBox()
        spin_vezes.setMinimum(1)
        spin_vezes.setMaximum(100)
        spin_vezes.setValue(1)
        layout.addWidget(spin_vezes)
        
        # Botões
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            vezes = spin_vezes.value()
            is_right = radio_direito.isChecked()
            
            # Minimizar janela antes de capturar
            self.showMinimized()
            
            # Criar overlay escuro para capturar clique
            if is_right:
                self.overlay = ClickCaptureOverlay(button_type='right')
                self.overlay.coordinate_captured.connect(self.on_right_click_captured)
            else:
                self.overlay = ClickCaptureOverlay()
                self.overlay.coordinate_captured.connect(lambda x, y: self.on_coordinate_captured(x, y, vezes))
            
            self.overlay.showFullScreen()
    
    def add_right_click_action(self):
        """Adicionar ação de clique com botão direito"""
        # Minimizar janela antes de capturar
        self.showMinimized()
        
        # Criar overlay escuro para capturar clique
        self.overlay = ClickCaptureOverlay(button_type='right')
        self.overlay.coordinate_captured.connect(self.on_right_click_captured)
        self.overlay.showFullScreen()
    
    def add_drag_action(self):
        """Adicionar ação de arrastar mouse"""
        # Minimizar janela antes de capturar
        self.showMinimized()
        
        # Criar overlay escuro para capturar início e fim do arraste
        self.overlay = DragCaptureOverlay()
        self.overlay.drag_captured.connect(self.on_drag_captured)
        self.overlay.showFullScreen()
    
    def on_coordinate_captured(self, x, y, vezes=1):
        self.acoes_list.add_acao({'type': 'click', 'x': x, 'y': y, 'vezes': vezes})
        # Restaurar janela
        self.showNormal()
        self.raise_()
        self.activateWindow()
    
    def on_right_click_captured(self, x, y):
        """Callback quando clique direito é capturado"""
        self.acoes_list.add_acao({'type': 'right_click', 'x': x, 'y': y})
        # Restaurar janela
        self.showNormal()
        self.raise_()
        self.activateWindow()
    
    def on_drag_captured(self, x1, y1, x2, y2):
        """Callback quando arraste é capturado"""
        self.acoes_list.add_acao({'type': 'drag', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2})
        # Restaurar janela
        self.showNormal()
        self.raise_()
        self.activateWindow()
    
    def add_sleep_action(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSpinBox, QDialogButtonBox
        
        # Diálogo customizado com descrição
        dialog = QDialog(self)
        dialog.setWindowTitle('Esperar')
        dialog.setFixedWidth(300)
        
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel('Tempo em milissegundos:'))
        
        spinbox = QSpinBox()
        spinbox.setMinimum(100)
        spinbox.setMaximum(60000)
        spinbox.setValue(1000)
        spinbox.setSingleStep(100)
        layout.addWidget(spinbox)
        
        # Label de descrição
        desc_label = QLabel('1000ms = 1 segundo')
        desc_label.setStyleSheet('color: #666; font-size: 11px; font-style: italic;')
        layout.addWidget(desc_label)
        
        # Atualizar descrição quando valor muda
        def update_desc(value):
            segundos = value / 1000.0
            if segundos == 1.0:
                desc_label.setText('1000ms = 1 segundo')
            else:
                desc_label.setText(f'{value}ms = {segundos:.1f} segundos')
        
        spinbox.valueChanged.connect(update_desc)
        
        # Botões
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.acoes_list.add_acao({'type': 'sleep', 'ms': spinbox.value()})
    
    
    def salvar(self):
        nome = self.nome_input.text().strip()
        tecla_input = self.atalho_input.text().strip()
        tipo_index = self.tipo_combo.currentIndex()
        
        if not nome:
            QMessageBox.warning(self, 'Erro', 'Preencha o nome do atalho!')
            return
        
        if not tecla_input:
            QMessageBox.warning(self, 'Erro', 'Configure como ativar o atalho!')
            return
        
        # Processar tecla baseado no tipo
        if tipo_index == 0:
            # Alt + Tecla - apenas salvar a tecla, Alt é automático
            tecla_atalho = tecla_input.upper()  # Normalizar para maiúscula
        else:
            # Atalho de texto
            tecla_atalho = tecla_input.lower()  # Normalizar para minúscula
        
        # Obter ações da lista
        acoes = self.acoes_list.get_acoes()
        
        if len(acoes) == 0:
            QMessageBox.warning(self, 'Erro', 'Adicione pelo menos uma ação!')
            return
        
        if self.shortcut_id:
            # Editando - verificar conflito excluindo o próprio
            conflitos = self.db.get_conflito_tecla(tecla_atalho, excluir_id=self.shortcut_id) if tecla_atalho else []
        else:
            # Criando novo
            conflitos = self.db.get_conflito_tecla(tecla_atalho) if tecla_atalho else []
        
        # Se há conflito, mostrar aviso
        if conflitos:
            nomes_conflito = ', '.join([f'"{c[1]}"' for c in conflitos])
            ativos_conflito = [c for c in conflitos if c[2] == 1]
            
            msg = QMessageBox()
            msg.setWindowTitle('Tecla já em uso')
            msg.setWindowFlags(msg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            
            if ativos_conflito:
                msg.setText(
                    f'O atalho "Alt + {tecla_atalho}" já está sendo usado por: {nomes_conflito}.\n\n'
                    f'Ao salvar, o(s) atalho(s) conflitante(s) serão desativados automaticamente.'
                )
            else:
                msg.setText(
                    f'O atalho "Alt + {tecla_atalho}" também está definido em: {nomes_conflito} (inativo).\n\n'
                    f'Se ativado, o outro será desativado automaticamente.'
                )
            
            btn_salvar = msg.addButton('Salvar assim mesmo', QMessageBox.ButtonRole.AcceptRole)
            btn_cancelar = msg.addButton('Cancelar', QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_cancelar)
            msg.exec()
            
            if msg.clickedButton() != btn_salvar:
                return
        
        if self.shortcut_id:
            # Editando
            self.db.update_shortcut(self.shortcut_id, nome, acoes, tecla_atalho)
            # Desativar conflitos da tecla (o atalho editado fica ativo)
            if tecla_atalho:
                self.db.desativar_conflitos_tecla(tecla_atalho, excluir_id=self.shortcut_id)
            msg = 'Atalho atualizado!'
        else:
            # Criando novo
            self.db.add_shortcut(nome, acoes, tecla_atalho)
            # Desativar conflitos (novo fica ativo, antigo desativa)
            if tecla_atalho:
                # Pegar o id do novo atalho
                novo_id = self.db.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
                self.db.desativar_conflitos_tecla(tecla_atalho, excluir_id=novo_id)
            msg = 'Atalho salvo!'
        
        notification = NotificationWidget(f'✓ {msg}')
        notification.show()
        
        if hasattr(self, 'menu_reference') and self.menu_reference:
            QTimer.singleShot(100, self.menu_reference.show_atalhos_tab)
        
        self.close()
    
    def closeEvent(self, event):
        print("AddShortcutWindow: closeEvent chamado")
        self.menu_reference = None
        event.accept()


class ClickCaptureOverlay(QWidget):
    coordinate_captured = pyqtSignal(int, int)
    
    def __init__(self, button_type='left'):
        super().__init__()
        self.button_type = button_type
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))  # Escuro semi-transparente
        
        # Desenhar instruções
        painter.setPen(QColor(255, 255, 255))
        if self.button_type == 'right':
            texto = "Clique onde deseja que o atalho clique com botão DIREITO\nESC para cancelar"
        else:
            texto = "Clique onde deseja que o atalho clique\nESC para cancelar"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, texto)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.globalPosition().x()
            y = event.globalPosition().y()
            self.coordinate_captured.emit(int(x), int(y))
            self.close()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()


class DragCaptureOverlay(QWidget):
    drag_captured = pyqtSignal(int, int, int, int)
    
    def __init__(self):
        super().__init__()
        self.start_pos = None
        self.end_pos = None
        self.is_dragging = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))  # Escuro semi-transparente
        
        # Desenhar instruções
        painter.setPen(QColor(255, 255, 255))
        if not self.is_dragging:
            texto = "Clique e SEGURE no ponto inicial, arraste até o ponto final e SOLTE\nESC para cancelar"
        else:
            texto = "Arraste até o ponto final e SOLTE o botão\nESC para cancelar"
        
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, texto)
        
        # Se está arrastando, desenhar linha em tempo real
        if self.start_pos and self.end_pos:
            painter.setPen(QPen(QColor(136, 194, 43, 255), 3))  # Verde, linha mais grossa
            painter.drawLine(self.start_pos, self.end_pos)
            
            # Desenhar círculos nos pontos
            painter.setBrush(QColor(136, 194, 43, 255))
            painter.drawEllipse(self.start_pos, 8, 8)
            painter.setBrush(QColor(255, 194, 43, 255))  # Amarelo no final
            painter.drawEllipse(self.end_pos, 8, 8)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Capturar ponto inicial
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.is_dragging = True
            self.update()
    
    def mouseMoveEvent(self, event):
        if self.is_dragging:
            # Atualizar ponto final enquanto arrasta
            self.end_pos = event.pos()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_dragging:
            # Capturar ponto final ao soltar
            self.end_pos = event.pos()
            
            # Converter para coordenadas de tela
            x1 = int(self.start_pos.x())
            y1 = int(self.start_pos.y())
            x2 = int(self.end_pos.x())
            y2 = int(self.end_pos.y())
            
            self.drag_captured.emit(x1, y1, x2, y2)
            self.close()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()


class AddTemplateWindow(QWidget):
    def __init__(self, db, menu_ref=None):
        print("AddTemplateWindow: __init__ chamado")
        super().__init__(None)  # Explicitamente sem parent
        print(f"AddTemplateWindow: super().__init__ concluído, parent={self.parent()}")
        
        self.db = db
        self.menu_reference = menu_ref
        
        # Janela completamente independente
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        print("AddTemplateWindow: Flags configuradas, chamando init_ui")
        self.init_ui()
        print("AddTemplateWindow: init_ui concluído")
    
    def closeEvent(self, event):
        print("AddTemplateWindow: closeEvent chamado")
        # Limpar referências
        self.menu_reference = None
        # Apenas aceitar
        event.accept()
        print("AddTemplateWindow: fechado com sucesso")
    
    def __del__(self):
        print("AddTemplateWindow: __del__ chamado (objeto sendo destruído)")
    
    def init_ui(self):
        self.setWindowTitle('Novo Template')
        self.setFixedSize(500, 400)
        
        layout = QVBoxLayout()
        
        title = QLabel('Criar Novo Template')
        title.setStyleSheet('font-weight: bold; font-size: 15px; padding: 10px;')
        layout.addWidget(title)
        
        layout.addWidget(QLabel('Nome do template:'))
        self.nome_input = QLineEdit()
        self.nome_input.setPlaceholderText('Ex: Saudação formal')
        layout.addWidget(self.nome_input)
        
        layout.addWidget(QLabel('Texto do template:'))
        self.texto_input = QTextEdit()
        self.texto_input.setPlaceholderText('Digite o texto que será inserido...')
        layout.addWidget(self.texto_input)
        
        layout.addWidget(QLabel('Atalho de texto (opcional):'))
        info_label = QLabel('💡 Digite o atalho e pressione ESPAÇO para expandir (ex: "otb" + espaço)')
        info_label.setStyleSheet('color: #666; font-size: 11px;')
        layout.addWidget(info_label)
        
        self.atalho_input = QLineEdit()
        self.atalho_input.setPlaceholderText('Ex: otb, abs, obg')
        layout.addWidget(self.atalho_input)
        
        btn_layout = QHBoxLayout()
        
        btn_salvar = QPushButton('✓ Salvar')
        btn_salvar.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        btn_salvar.clicked.connect(self.salvar)
        btn_layout.addWidget(btn_salvar)
        
        btn_cancelar = QPushButton('✗ Cancelar')
        btn_cancelar.clicked.connect(self.close)
        btn_layout.addWidget(btn_cancelar)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def salvar(self):
        nome = self.nome_input.text().strip()
        texto = self.texto_input.toPlainText().strip()
        atalho = self.atalho_input.text().strip() or None
        
        if not nome or not texto:
            QMessageBox.warning(self, 'Erro', 'Preencha pelo menos o nome e o texto!')
            return
        
        # Salvar template
        self.db.add_template(nome, texto, atalho)
        
        # Mostrar notificação não-intrusiva
        self.notification = NotificationWidget('✓ Template salvo!')
        self.notification.show()
        
        # Atualizar menu se estiver aberto (sem fechar nada)
        if self.menu_reference and hasattr(self.menu_reference, 'show_templates_tab'):
            QTimer.singleShot(100, self.menu_reference.show_templates_tab)
        
        # Fechar janela por último
        self.close()


class EditTemplateWindow(QWidget):
    """Janela para editar template existente"""
    def __init__(self, db, menu_ref=None, template_id=None, nome='', texto='', atalho=''):
        super().__init__(None)
        
        self.db = db
        self.menu_reference = menu_ref
        self.template_id = template_id
        
        # Janela completamente independente
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        
        self.init_ui(nome, texto, atalho)
    
    def closeEvent(self, event):
        # Limpar referências
        self.menu_reference = None
        event.accept()
    
    def init_ui(self, nome, texto, atalho):
        self.setWindowTitle('Editar Template')
        self.setFixedSize(500, 400)
        
        layout = QVBoxLayout()
        
        title = QLabel('Editar Template')
        title.setStyleSheet('font-weight: bold; font-size: 15px; padding: 10px;')
        layout.addWidget(title)
        
        layout.addWidget(QLabel('Nome do template:'))
        self.nome_input = QLineEdit()
        self.nome_input.setText(nome)
        self.nome_input.setPlaceholderText('Ex: Saudação formal')
        layout.addWidget(self.nome_input)
        
        layout.addWidget(QLabel('Texto do template:'))
        self.texto_input = QTextEdit()
        self.texto_input.setPlainText(texto)
        self.texto_input.setPlaceholderText('Digite o texto que será inserido...')
        layout.addWidget(self.texto_input)
        
        layout.addWidget(QLabel('Atalho de texto (opcional):'))
        info_label = QLabel('💡 Digite o atalho e pressione ESPAÇO para expandir (ex: "otb" + espaço)')
        info_label.setStyleSheet('color: #666; font-size: 11px;')
        layout.addWidget(info_label)
        
        self.atalho_input = QLineEdit()
        self.atalho_input.setText(atalho if atalho else '')
        self.atalho_input.setPlaceholderText('Ex: otb, abs, obg')
        layout.addWidget(self.atalho_input)
        
        btn_layout = QHBoxLayout()
        
        btn_salvar = QPushButton('✓ Salvar')
        btn_salvar.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                padding: 10px;
                font-weight: bold;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        btn_salvar.clicked.connect(self.salvar)
        btn_layout.addWidget(btn_salvar)
        
        btn_cancelar = QPushButton('✗ Cancelar')
        btn_cancelar.clicked.connect(self.close)
        btn_layout.addWidget(btn_cancelar)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def salvar(self):
        nome = self.nome_input.text().strip()
        texto = self.texto_input.toPlainText().strip()
        atalho = self.atalho_input.text().strip() or None
        
        if not nome or not texto:
            QMessageBox.warning(self, 'Erro', 'Preencha pelo menos o nome e o texto!')
            return
        
        # Atualizar template no banco
        self.db.update_template(self.template_id, nome, texto, atalho)
        
        # Mostrar notificação
        self.notification = NotificationWidget('✓ Template atualizado!')
        self.notification.show()
        
        # Atualizar menu se estiver aberto
        if self.menu_reference and hasattr(self.menu_reference, 'show_templates_tab'):
            QTimer.singleShot(100, self.menu_reference.show_templates_tab)
        
        # Fechar janela
        self.close()

def main():
    app = QApplication(sys.argv)
    
    # Criar instância de autenticação
    firebase = FirebaseAuth()
    
    # Variável global para manter referência
    global circle
    circle = None
    
    # Mostrar tela de login
    login_window = LoginWindow(firebase)
    
    # Quando login for bem-sucedido, criar o círculo
    def on_login_success(user_data):
        global circle
        print(f"DEBUG: Login success! User: {user_data}")
        
        # Criar database com dados do usuário
        db = Database(user_id=user_data['uid'], user_setor=user_data['setor'])
        
        # Criar círculo com usuário logado
        circle = FloatingCircle(db, firebase, user_data)
        circle.show()
        circle.raise_()  # Trazer para frente
        circle.activateWindow()  # Ativar
        circle.setWindowState(circle.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)

        print("DEBUG: Círculo criado e mostrado")
        print(f"DEBUG: isVisible = {circle.isVisible()}")
        print(f"DEBUG: isActiveWindow = {circle.isActiveWindow()}")
    
    login_window.login_success.connect(on_login_success)
    login_window.show()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()