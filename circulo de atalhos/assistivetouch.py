import sys
import sqlite3
import time
import json
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QTextEdit, QMessageBox, QScrollArea, QListWidget, 
                              QListWidgetItem, QSpinBox, QComboBox, QCheckBox)
from PyQt6.QtCore import Qt, QPoint, QTimer, QObject, pyqtSignal, QRect
from PyQt6.QtGui import QCursor, QPainter, QColor
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
    def __init__(self):
        self.conn = sqlite3.connect('assistivetouch.db', check_same_thread=False)
        self.create_tables()
import sqlite3
import time
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QTextEdit, QMessageBox, QScrollArea, QListWidget, QListWidgetItem)
from PyQt6.QtCore import Qt, QPoint, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QCursor
from pynput import keyboard
from pynput.keyboard import Key, Controller

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('assistivetouch.db', check_same_thread=False)
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
                CREATE TABLE templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    texto TEXT NOT NULL,
                    atalho TEXT
                )
            ''')
        
        # Tabela de atalhos (shortcuts de automação)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shortcuts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                ativo INTEGER DEFAULT 1,
                acoes TEXT NOT NULL,
                tecla_atalho TEXT
            )
        ''')
        
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
    
    def add_template(self, nome, texto, atalho=None):
        cursor = self.conn.cursor()
        cursor.execute('INSERT INTO templates (nome, texto, atalho) VALUES (?, ?, ?)', 
                      (nome, texto, atalho))
        self.conn.commit()
    
    def get_templates(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, nome, texto, atalho FROM templates')
        return cursor.fetchall()
    
    def search_templates(self, query):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, nome, texto, atalho FROM templates 
            WHERE nome LIKE ? OR texto LIKE ?
        ''', (f'%{query}%', f'%{query}%'))
        return cursor.fetchall()
    
    def delete_template(self, id):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM templates WHERE id = ?', (id,))
        self.conn.commit()
    
    # Métodos para Shortcuts
    def add_shortcut(self, nome, acoes, tecla_atalho=None):
        cursor = self.conn.cursor()
        acoes_json = json.dumps(acoes)
        cursor.execute('INSERT INTO shortcuts (nome, ativo, acoes, tecla_atalho) VALUES (?, 1, ?, ?)', 
                      (nome, acoes_json, tecla_atalho))
        self.conn.commit()
    
    def update_shortcut(self, id, nome, acoes, tecla_atalho=None):
        cursor = self.conn.cursor()
        acoes_json = json.dumps(acoes)
        cursor.execute('UPDATE shortcuts SET nome = ?, acoes = ?, tecla_atalho = ? WHERE id = ?',
                      (nome, acoes_json, tecla_atalho, id))
        self.conn.commit()
    
    def get_shortcuts(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, nome, ativo, acoes, tecla_atalho FROM shortcuts')
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
        cursor.execute('SELECT ativo FROM shortcuts WHERE id = ?', (id,))
        result = cursor.fetchone()
        if result:
            novo_status = 0 if result[0] == 1 else 1
            cursor.execute('UPDATE shortcuts SET ativo = ? WHERE id = ?', (novo_status, id))
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
                if key == Key.right:
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
                
                # Digitar
                self.keyboard_controller.type(texto)
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
                    self.keyboard_controller.type(template[2])
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
                self.execute_shortcut(shortcut['acoes'])
                return
    
    def execute_shortcut(self, acoes):
        print(f"Executando {len(acoes)} ações...")
        
        def run():
            try:
                time.sleep(0.1)
                for i, acao in enumerate(acoes):
                    print(f"Ação {i+1}: {acao['type']}")
                    
                    if acao['type'] == 'click':
                        self.mouse_controller.position = (acao['x'], acao['y'])
                        self.mouse_controller.click(Button.left, 1)
                        
                    elif acao['type'] == 'type':
                        self.keyboard_controller.type(acao['text'])
                        
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
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.dragging = False
        self.drag_start_position = QPoint()
        self.click_position = QPoint()
        self.menu = None
        self.init_ui()
    
    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Criar widget container ao invés de usar botão
        self.container = QWidget(self)
        self.container.setFixedSize(60, 60)
        self.container.setStyleSheet("""
            QWidget {
                background-color: rgba(100, 100, 100, 180);
                border-radius: 30px;
            }
            QWidget:hover {
                background-color: rgba(80, 80, 80, 200);
            }
        """)
        
        # Label com ícone
        label = QLabel('⚙', self.container)
        label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setGeometry(0, 0, 60, 60)
        
        layout = QVBoxLayout()
        layout.addWidget(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)
        
        self.setFixedSize(60, 60)
        
        position = self.db.get_position()
        if position:
            self.move(position[0], position[1])
        else:
            screen = QApplication.primaryScreen().geometry()
            self.move(screen.width() - 100, screen.height() // 2)
    
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
                # Foi arraste - salvar posição
                pos = self.pos()
                self.db.save_position(pos.x(), pos.y())
            else:
                # Foi clique - abrir menu
                self.show_menu()
            
            self.dragging = False
            event.accept()
    
    def show_menu(self):
        print(f"Círculo: show_menu chamado. Menu existe? {self.menu is not None}")
        if self.menu:
            print(f"Círculo: Menu está visível? {self.menu.isVisible()}")
        
        # Se o menu já está aberto, apenas fechar
        if self.menu and self.menu.isVisible():
            print("Círculo: Menu já está aberto, fechando...")
            self.menu.close()
            self.menu = None
            return
        
        # Caso contrário, abrir novo menu
        print("Círculo: Abrindo novo menu...")
        if self.menu:
            self.menu.close()
        
        self.menu = MainMenu(self.db, self)
        
        # Conectar evento de fechar para limpar referência
        self.menu.destroyed.connect(lambda: setattr(self, 'menu', None))
        
        menu_x = self.x() - 360
        menu_y = self.y()
        self.menu.move(menu_x, menu_y)
        self.menu.show()
        print("Círculo: Menu mostrado")


class MainMenu(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.circle_parent = parent
        self.add_window = None  # Manter referência
        self.init_ui()
    
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
        
        self.btn_templates.clicked.connect(self.show_templates_tab)
        self.btn_atalhos.clicked.connect(self.show_atalhos_tab)
        
        tabs_layout.addWidget(self.btn_templates)
        tabs_layout.addWidget(self.btn_atalhos)
        
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
        
        self.show_templates_tab()
        self.setFixedHeight(400)
    
    def show_templates_tab(self):
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.btn_templates.setStyleSheet("""
            QPushButton {
                background-color: #406e54;
                color: white;
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
            }
            QPushButton:hover {
                background-color: #b8ada6;
            }
        """)
        
        btn_add = QPushButton('Adicionar template')
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #406e54;
                border: none;
                padding: 10px;
                text-align: left;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(64, 110, 84, 0.1);
            }
        """)
        btn_add.clicked.connect(self.add_template)
        self.content_layout.addWidget(btn_add)
        
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #d0c7c0;")
        self.content_layout.addWidget(line)
        
        templates = self.db.get_templates()
        
        if templates:
            for template in templates:
                template_widget = QWidget()
                template_widget.setStyleSheet("""
                    QWidget {
                        background-color: white;
                        border: 1px solid #d0c7c0;
                        border-radius: 8px;
                    }
                """)
                
                template_layout = QVBoxLayout()
                template_layout.setContentsMargins(12, 10, 12, 10)
                template_layout.setSpacing(4)
                
                # Primeira linha: Nome e botão deletar
                first_line = QHBoxLayout()
                first_line.setSpacing(8)
                
                nome_label = QLabel(template[1])
                nome_label.setStyleSheet('font-weight: bold; font-size: 13px; color: #2d2d2d;')
                first_line.addWidget(nome_label, stretch=1)
                
                # Botão deletar pequeno
                btn_delete = QPushButton('🗑')
                btn_delete.setFixedSize(24, 24)
                btn_delete.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #82414c;
                        border: none;
                        border-radius: 4px;
                        font-size: 14px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: rgba(130, 65, 76, 0.15);
                    }
                """)
                btn_delete.clicked.connect(lambda checked, tid=template[0]: self.delete_template(tid))
                first_line.addWidget(btn_delete)
                
                template_layout.addLayout(first_line)
                
                # Segunda linha: Preview do texto
                texto_preview = template[2][:60] + '...' if len(template[2]) > 60 else template[2]
                texto_label = QLabel(texto_preview)
                texto_label.setStyleSheet('font-size: 11px; color: #777;')
                texto_label.setWordWrap(True)
                template_layout.addWidget(texto_label)
                
                # Terceira linha: Atalho (se existir)
                if template[3]:
                    atalho_label = QLabel(f'Atalho: ⚡ {template[3]}')
                    atalho_label.setStyleSheet('font-size: 10px; color: #88c22b; font-weight: bold; margin-top: 2px;')
                    template_layout.addWidget(atalho_label)
                
                template_widget.setLayout(template_layout)
                self.content_layout.addWidget(template_widget)
        else:
            empty_label = QLabel('Nenhum template ainda.\nClique em "Adicionar template" para criar o primeiro!')
            empty_label.setStyleSheet('color: #888; padding: 30px; font-size: 12px;')
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(empty_label)
        
        self.content_layout.addStretch()
    
    def delete_template(self, template_id):
        msg = QMessageBox(self)
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
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.db.delete_template(template_id)
            self.show_templates_tab()
    
    def show_atalhos_tab(self):
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.btn_templates.setStyleSheet("""
            QPushButton {
                background-color: #c8bfb8;
                color: #3d3d3d;
                border: none;
                padding: 12px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #b8ada6;
            }
        """)
        self.btn_atalhos.setStyleSheet("""
            QPushButton {
                background-color: #406e54;
                color: white;
                border: none;
                padding: 12px;
                font-size: 13px;
                font-weight: bold;
            }
        """)
        
        btn_add = QPushButton('Adicionar atalho')
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #406e54;
                border: none;
                padding: 10px;
                text-align: left;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(64, 110, 84, 0.1);
            }
        """)
        btn_add.clicked.connect(self.add_shortcut)
        self.content_layout.addWidget(btn_add)
        
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #d0c7c0;")
        self.content_layout.addWidget(line)
        
        shortcuts = self.db.get_shortcuts()
        
        if shortcuts:
            for shortcut in shortcuts:
                shortcut_widget = QWidget()
                shortcut_widget.setStyleSheet("""
                    QWidget {
                        background-color: white;
                        border: 1px solid #d0c7c0;
                        border-radius: 8px;
                    }
                """)
                
                shortcut_layout = QVBoxLayout()
                shortcut_layout.setContentsMargins(12, 10, 12, 10)
                shortcut_layout.setSpacing(4)
                
                # Primeira linha: Nome e Toggle
                first_line = QHBoxLayout()
                first_line.setSpacing(8)
                
                nome_label = QLabel(shortcut['nome'])
                nome_label.setStyleSheet('font-weight: bold; font-size: 13px; color: #2d2d2d;')
                first_line.addWidget(nome_label, stretch=1)
                
                # Toggle switch estilo iOS
                toggle = QPushButton()
                toggle.setCheckable(True)
                toggle.setChecked(shortcut['ativo'])
                toggle.setFixedSize(50, 26)
                
                def update_toggle_style(btn, checked):
                    if checked:
                        btn.setStyleSheet("""
                            QPushButton {
                                background-color: #88c22b;
                                border-radius: 13px;
                                border: none;
                            }
                            QPushButton::before {
                                content: '';
                            }
                        """)
                    else:
                        btn.setStyleSheet("""
                            QPushButton {
                                background-color: #ccc;
                                border-radius: 13px;
                                border: none;
                            }
                        """)
                
                update_toggle_style(toggle, shortcut['ativo'])
                toggle.clicked.connect(lambda checked, sid=shortcut['id'], t=toggle: (
                    self.toggle_shortcut_status(sid),
                    update_toggle_style(t, checked)
                ))
                first_line.addWidget(toggle)
                
                shortcut_layout.addLayout(first_line)
                
                # Segunda linha: Resumo das ações e atalho
                acoes_texto = f"{len(shortcut['acoes'])} ações"
                if shortcut.get('tecla_atalho'):
                    tecla = shortcut['tecla_atalho']
                    # Verificar se é apenas 1-2 caracteres (Alt + tecla)
                    if len(tecla) <= 2 and tecla.isalpha():
                        acoes_texto += f" • Comando: Alt+{tecla}"
                    else:
                        acoes_texto += f" • Atalho: {tecla}"
                acoes_label = QLabel(acoes_texto)
                acoes_label.setStyleSheet('font-size: 11px; color: #777;')
                shortcut_layout.addWidget(acoes_label)
                
                # Botões de ação
                buttons_layout = QHBoxLayout()
                buttons_layout.setSpacing(5)
                
                btn_edit = QPushButton('✏ Editar')
                btn_edit.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #406e54;
                        border: 1px solid #406e54;
                        padding: 4px 8px;
                        border-radius: 3px;
                        font-size: 10px;
                    }
                    QPushButton:hover {
                        background-color: rgba(64, 110, 84, 0.1);
                    }
                """)
                btn_edit.clicked.connect(lambda checked, s=shortcut: self.edit_shortcut(s))
                buttons_layout.addWidget(btn_edit)
                
                btn_delete = QPushButton('🗑 Excluir')
                btn_delete.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #82414c;
                        border: 1px solid #82414c;
                        padding: 4px 8px;
                        border-radius: 3px;
                        font-size: 10px;
                    }
                    QPushButton:hover {
                        background-color: rgba(130, 65, 76, 0.1);
                    }
                """)
                btn_delete.clicked.connect(lambda checked, sid=shortcut['id']: self.delete_shortcut(sid))
                buttons_layout.addWidget(btn_delete)
                
                buttons_layout.addStretch()
                shortcut_layout.addLayout(buttons_layout)
                
                shortcut_widget.setLayout(shortcut_layout)
                self.content_layout.addWidget(shortcut_widget)
        else:
            empty_label = QLabel('Nenhum atalho ainda.\nClique em "Adicionar atalho" para criar o primeiro!')
            empty_label.setStyleSheet('color: #888; padding: 30px; font-size: 12px;')
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content_layout.addWidget(empty_label)
        
        self.content_layout.addStretch()
    
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
        msg = QMessageBox(self)
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
        
        msg.exec()
        
        if msg.clickedButton() == btn_sim:
            self.db.delete_shortcut(shortcut_id)
            self.show_atalhos_tab()
    
    def toggle_shortcut_status(self, shortcut_id):
        self.db.toggle_shortcut(shortcut_id)
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
        
        # Lista de ações
        self.acoes_list = QListWidget()
        self.acoes_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.acoes_list)
        
        # Atualizar lista se estiver editando
        if self.acoes:
            self.update_acoes_list()
        
        # Botões para adicionar ações
        acoes_buttons = QHBoxLayout()
        
        btn_click = QPushButton('+ Clique')
        btn_click.clicked.connect(self.add_click_action)
        acoes_buttons.addWidget(btn_click)
        
        btn_type = QPushButton('+ Digitar')
        btn_type.clicked.connect(self.add_type_action)
        acoes_buttons.addWidget(btn_type)
        
        btn_sleep = QPushButton('+ Esperar')
        btn_sleep.clicked.connect(self.add_sleep_action)
        acoes_buttons.addWidget(btn_sleep)
        
        layout.addLayout(acoes_buttons)
        
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
            self.acoes.append({'type': 'type', 'text': texto})
            self.update_acoes_list()
    
    def add_click_action(self):
        self.overlay = ClickCaptureOverlay()
        self.overlay.coordinate_captured.connect(self.on_coordinate_captured)
        self.overlay.showFullScreen()
    
    def on_coordinate_captured(self, x, y):
        self.acoes.append({'type': 'click', 'x': x, 'y': y})
        self.update_acoes_list()
    
    def add_sleep_action(self):
        from PyQt6.QtWidgets import QInputDialog
        tempo, ok = QInputDialog.getInt(self, 'Esperar', 'Tempo em milissegundos:', 1000, 100, 60000, 100)
        if ok:
            self.acoes.append({'type': 'sleep', 'ms': tempo})
            self.update_acoes_list()
    
    def update_acoes_list(self):
        self.acoes_list.clear()
        for i, acao in enumerate(self.acoes):
            if acao['type'] == 'click':
                texto = f"{i+1}. Clique em ({acao['x']}, {acao['y']})"
            elif acao['type'] == 'sleep':
                texto = f"{i+1}. Esperar {acao['ms']}ms"
            elif acao['type'] == 'type':
                preview = acao['text'][:30] + '...' if len(acao['text']) > 30 else acao['text']
                texto = f"{i+1}. Digitar: {preview}"
            self.acoes_list.addItem(texto)
    
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
        
        if len(self.acoes) == 0:
            QMessageBox.warning(self, 'Erro', 'Adicione pelo menos uma ação!')
            return
        
        if self.shortcut_id:
            # Editando
            self.db.update_shortcut(self.shortcut_id, nome, self.acoes, tecla_atalho)
            msg = 'Atalho atualizado!'
        else:
            # Criando novo
            self.db.add_shortcut(nome, self.acoes, tecla_atalho)
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
    
    def __init__(self):
        super().__init__()
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
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 
                        "Clique onde deseja que o atalho clique\nESC para cancelar")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.globalPosition().x()
            y = event.globalPosition().y()
            self.coordinate_captured.emit(int(x), int(y))
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
        if self.parent_menu and hasattr(self.parent_menu, 'show_templates_tab'):
            QTimer.singleShot(100, self.parent_menu.show_templates_tab)
        
        # Fechar janela por último
        self.close()


def main():
    app = QApplication(sys.argv)
    
    # CRITICAL: Impedir que o app feche quando a última janela fecha
    app.setQuitOnLastWindowClosed(False)
    
    db = Database()
    
    circle = FloatingCircle(db)
    circle.show()
    
    keyboard_listener = KeyboardListener(db)
    keyboard_listener.start()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()