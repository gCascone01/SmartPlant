from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QTextEdit, QSpacerItem, QSizePolicy, QFrame # type: ignore
from PyQt5.QtGui import QPixmap, QFont, QMovie # type: ignore
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer # type: ignore


class Display(QWidget):
    """
    Main display widget for the plant screen.
    Shows:
    - GIF / PNG expressions
    - QR code and text
    - LLM text responses
    - Connection status messages
    """

    llm_text_signal = pyqtSignal(str,bool)
    llm_talk_signal = pyqtSignal(str)
    display_finished_signal = pyqtSignal()
    clear_text_signal = pyqtSignal(str,bool)
    connection_signal = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        
        self.main_layout = QVBoxLayout(self)

        self.setMaximumHeight(self.screen().size().height())
        self.info = False
        self.gif = None
        self.gifs = {}
        self.server_connected = False
        self.signal = False


        self.preload_gif()

        self.setWindowTitle("Flower Info")
        self.setStyleSheet("background-color: black;")
        self.showFullScreen()

        self.spacer = QSpacerItem(
            20, 10, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.main_layout.addItem(self.spacer)

        # Label for expressions (GIF/PNG)
        self.expression_label = QLabel(self)
        self.expression_label.setAlignment(Qt.AlignCenter)
        self.expression_label.setStyleSheet("background-color: black;")
        self.main_layout.addWidget(self.expression_label)

        # Text frame for info and LLM text
        text_layout = QVBoxLayout()
        text_frame = QWidget(self)
        text_frame.setLayout(text_layout)
        text_frame.setStyleSheet("background-color: black;")
        self.main_layout.addWidget(text_frame)

        # Info label (sensor thresholds etc.)
        self.info_label = QLabel("Loading...", self)
        self.info_label.setFont(QFont("Courier", 12))
        self.info_label.setStyleSheet("color: white; background-color: black;")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setVisible(False)
        text_layout.addWidget(self.info_label)

        # QR code image
        self.qr = QLabel(self)
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setStyleSheet("background-color: black;")
        pixmap = QPixmap("qr.png")
        pixmap = pixmap.scaled(
            120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.qr.setPixmap(pixmap)
        self.main_layout.addWidget(self.qr)

        # QR code text
        self.qr_text = QLabel("Σκάναρέ με για να μιλήσεις μαζί μου 💚")
        self.qr_text.setAlignment(Qt.AlignCenter)
        self.qr_text.setStyleSheet("color: white; font-size: 15px;")
        self.main_layout.addWidget(self.qr_text)

        # No connection QR code text
        self.no_conection = QLabel("Λυπάμαι, δεν μπορώ να σου μιλήσω τώρα. Μπορούμε να τα πούμε αργότερα 💚")
        self.no_conection.setAlignment(Qt.AlignCenter)
        self.no_conection.setStyleSheet("color: red; font-size: 14px;")
        self.no_conection.setVisible(False)
        self.main_layout.addWidget(self.no_conection)

        # LLM text response box
        self.llm_text_edit = QTextEdit(self)
        self.llm_text_edit.setReadOnly(True)
        self.llm_text_edit.setFrameStyle(QFrame.NoFrame)
        self.llm_text_edit.setFont(QFont("Noto Color Emoji", 20))
        self.llm_text_edit.setStyleSheet(
            "color: #00ffcc; background-color: black;  border: none;")
        text_layout.addWidget(self.llm_text_edit)

        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.timeout.connect(self.auto_scroll_step)
        self.scroll_direction=1
        self.scroll_step=2
        self.scroll_pause = False   
        self.pause_duration = 3000 

        # Signals connections
        self.llm_text_edit.textChanged.connect(self.check_and_start_auto_scroll)
        self.llm_text_signal.connect(self.LlmTextDisplay)
        self.llm_talk_signal.connect(self.talk)
        self.clear_text_signal.connect(self.LlmTextDisplay)
        self.connection_signal.connect(self.connection_info)
        self.screen().geometryChanged.connect(self.screen_resized)

        # Initial loading expression
        self.ExpressionRefresh("expressions/loading.png", False)

    def check_and_start_auto_scroll(self):
        scrollbar = self.llm_text_edit.verticalScrollBar()

        if scrollbar.maximum() <= 0:
            self.auto_scroll_timer.stop()
            return

        if not self.auto_scroll_timer.isActive():
            self.scroll_direction = 1
            self.scroll_pause = True
            self.auto_scroll_timer.start(50)  

            QTimer.singleShot(self.pause_duration, self.end_pause)


    def auto_scroll_step(self):
        if self.scroll_pause:
            return

        scrollbar = self.llm_text_edit.verticalScrollBar()
        value = scrollbar.value()
        minimum = scrollbar.minimum()
        maximum = scrollbar.maximum()

        if self.scroll_direction > 0:
            if value >= maximum:
                self.scroll_direction = -1
                self.scroll_pause = True
                QTimer.singleShot(self.pause_duration, self.end_pause)
            else:
                scrollbar.setValue(min(value + self.scroll_step, maximum))

        else:
            if value <= minimum:
                self.scroll_direction = 1
                self.scroll_pause = True
                QTimer.singleShot(self.pause_duration, self.end_pause)
            else:
                scrollbar.setValue(max(value - self.scroll_step, minimum))

    def end_pause(self):
        self.scroll_pause = False

    def screen_resized(self):
        """Update maximum height when screen size changes."""
        self.setMaximumHeight(self.screen().size().height())

    def resizeEvent(self, event):
        """
        Handle window resize events.
        Adjust info label font size for small screens and reload GIF sizes.
        """
        if self.frameGeometry().height()<650 and hasattr(self, 'info_label'):
            self.info_label.setFont(QFont("Courier", 9))
        elif hasattr(self, 'info_label'):
            self.info_label.setFont(QFont("Courier", 12))

        self.preload_gif()
        event.accept()

    def connection_info(self, connected):
        """
        Update the UI based on connection status with the server.
        """
        if connected:
            self.server_connected = True
            self.preload_gif()
            self.no_conection.setVisible(False)
            self.qr.setVisible(True)
            self.qr_text.setVisible(True)
            self.preload_gif()
            self.main_layout.insertSpacerItem(0, self.spacer)
        else:
            self.server_connected = False
            self.preload_gif()
            self.qr.setVisible(False)
            self.qr_text.setVisible(False)
            self.no_conection.setVisible(True)
            self.main_layout.removeItem(self.spacer)
            self.preload_gif()


    def ExpressionRefresh(self, path, last):
        """
        Update the expression label with either a PNG or GIF.
        If it's a PNG, it is scaled and shown for a few seconds.
        If it's a GIF, a preloaded QMovie is used.
        """
        if path.lower().endswith('.png'):
            # Decide divisor based on info text visibility and connection status
            if self.info != self.server_connected:
                self.divisor = 2.15
            elif not self.info and not self.server_connected:
                self.divisor = 1.95
            elif self.info and self.server_connected:
                if self.isFullScreen():
                    self.divisor = 2.15
                else:
                    self.divisor = 2.65

            self.height = int(self.frameGeometry().height() / self.divisor)

            if self.height < 100:
                self.height = 100

            try:
                self.expression_label.clear()
                pixmap = QPixmap(path)
                pixmap = pixmap.scaled(
                    self.height, self.height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.expression_label.setPixmap(pixmap)

                # Emit signal after some time if this is not the loading image
                if path != "expressions/loading.png" and not last:
                    self.signal = True
                    QTimer.singleShot(3000, lambda: self.display_finished_signal.emit())
                elif last:
                    self.signal = True
                    QTimer.singleShot(8000, lambda: self.display_finished_signal.emit())

            except Exception as e:
                print(f"Error loading image {path}: {e}")
        else:
            #Show GIF using preloaded QMovie
            try:
                self.frames = get_frames(path)
                self.expression_label.setMovie(self.gifs[path])
                self.gif = self.gifs[path]
                self.gif.start()
            except Exception as e:
                print(f"Error loading gif {path}: {e}")


    def talk(self, path):
        """
        Show the 'talk' image when the plant is replying.
        """
        if self.info != self.server_connected:
            self.divisor = 2.15
        elif not self.info and not self.server_connected:
            self.divisor = 1.95
        elif self.info and self.server_connected:

            if self.isFullScreen():
                self.divisor = 2.15
            else:
                self.divisor = 2.65

        self.height = int(self.frameGeometry().height() / self.divisor)

        if self.height < 100:
            self.height = 100

        if self.signal:
            self.signal = False

        try:
            self.expression_label.clear()
            pixmap = QPixmap(path)
            pixmap = pixmap.scaled(
                self.height, self.height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.expression_label.setPixmap(pixmap)

        except Exception as e:
            print(f"Error loading image {path}: {e}")

    def frames_played(self, frame_index):
        """
        Callback for GIF frame changes. When the last frame is reached,
        stop the GIF and emit the display_finished_signal.
        """

        if frame_index == self.frames - 1:
            self.gif.stop()
            self.display_finished_signal.emit()

    def InfoTextRefresh(self, info):
        """Update sensor info text."""
        self.info_label.setText(info)

    def LlmTextDisplay(self, llm_text, center):
        """
        Display LLM text.
        """
        if center:
            self.llm_text_edit.setHtml(f'<div align="center">{llm_text}</div>')
        else:
            self.llm_text_edit.setPlainText(llm_text)

    def keyPressEvent(self, event):
        """
        Handle key presses:
        - ESC: close app
        - A: toggle info text
        - F: toggle fullscreen
        """
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_A:
            self.info = not self.info
            self.info_label.setVisible(self.info)

            if self.info:
                self.main_layout.removeItem(self.spacer)
                self.preload_gif()
            else:
                self.preload_gif()
                self.main_layout.insertSpacerItem(0, self.spacer)
        elif event.key() == Qt.Key_F:

            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()

    def preload_gif(self):
        """
        Preload all GIFs with the proper scaled size and connect their frameChanged signals.
        """
        if self.info != self.server_connected:
            self.divisor = 2.15
        elif not self.info and not self.server_connected:
            self.divisor = 1.95
        elif self.info and self.server_connected:
            if self.isFullScreen():
                self.divisor = 2.15
            else:
                self.divisor = 2.65

        self.height = int(self.frameGeometry().height() / self.divisor)

        if self.height < 100:
            self.height = 100

        gifs_paths = [
            "expressions/hot.gif",
            "expressions/cloud.gif",
            "expressions/cold.gif",
            "expressions/cold_2.gif",
            "expressions/cold_3.gif",
            "expressions/cry.gif",
            "expressions/droplet.gif",
            "expressions/good.gif",
            "expressions/happy.gif",
            "expressions/neutral.gif",
            "expressions/sad.gif",
            "expressions/slightly_happy.gif",
            "expressions/sweat.gif",
            "expressions/sweat_2.gif",
            "expressions/upside_down.gif",
            "expressions/warning.gif",
            "expressions/leafs.gif",
            "expressions/wink.gif",
            "expressions/grin.gif",
            "expressions/relieved.gif",
            "expressions/sun.gif",
            "expressions/fire.gif",
            "expressions/dotted-line.gif",
            "expressions/angry.gif",
            "expressions/cowboy.gif",
            "expressions/dizzy-face.gif",
            "expressions/expressionless.gif",
            "expressions/nerd-face.gif",
            "expressions/sunglasses-face.gif",
            "expressions/tired.gif",
            "expressions/unamused.gif",
            "expressions/x-eyes.gif",
            "expressions/grimacing.gif",
            "expressions/grinning.gif",
            "expressions/raised-eyebrow.gif",
            "expressions/rolling-eyes.gif",
            "expressions/thinking-face.gif",
            "expressions/concerned.gif",
            "expressions/exhale.gif",
            "expressions/pensive.gif",
            "expressions/worried.gif",
            "expressions/battery-full.gif",
            "expressions/Battery-low.gif",
            "expressions/cloud-with-lighting.gif",
            "expressions/leaves.gif",
            "expressions/plant.gif",
            "expressions/rain-cloud.gif",
        ]

        for gif_p in gifs_paths:
            gif = QMovie(gif_p)
            gif.setScaledSize(QSize(self.height, self.height))
            gif.setCacheMode(QMovie.CacheAll)
            gif.frameChanged.connect(self.frames_played)
            self.gifs[gif_p] = gif


def get_frames(expression):
    """
    Return the number of frames for a given GIF expression, used to detect end of animation.
    """
    frames_map = {
        "expressions/hot.gif": 42,
        "expressions/cloud.gif": 86,
        "expressions/cold.gif": 61,
        "expressions/cold_2.gif": 71,
        "expressions/cold_3.gif": 135,
        "expressions/cry.gif": 55,
        "expressions/droplet.gif": 32,
        "expressions/good.gif": 24,
        "expressions/happy.gif": 49,
        "expressions/neutral.gif": 17,
        "expressions/sad.gif": 90,
        "expressions/slightly_happy.gif": 58,
        "expressions/sweat.gif": 84,
        "expressions/sweat_2.gif": 68,
        "expressions/upside_down.gif": 46,
        "expressions/warning.gif": 48,
        "expressions/leafs.gif": 106,
        "expressions/wink.gif": 56,
        "expressions/grin.gif": 68,
        "expressions/relieved.gif": 80,
        "expressions/sun.gif": 56,
        "expressions/fire.gif": 33,
        "expressions/dotted-line.gif": 94,
        "expressions/angry.gif": 41,
        "expressions/cowboy.gif": 43,
        "expressions/dizzy-face.gif": 60,
        "expressions/expressionless.gif": 36,
        "expressions/nerd-face.gif": 66,
        "expressions/sunglasses-face.gif": 63,
        "expressions/tired.gif": 57,
        "expressions/unamused.gif": 81,
        "expressions/x-eyes.gif": 85,
        "expressions/grimacing.gif": 37,
        "expressions/grinning.gif": 76,
        "expressions/raised-eyebrow.gif": 36,
        "expressions/rolling-eyes.gif": 82,
        "expressions/thinking-face.gif": 60,
        "expressions/concerned.gif": 128,
        "expressions/exhale.gif": 65,
        "expressions/pensive.gif": 61,
        "expressions/worried.gif": 62,
        "expressions/battery-full.gif": 52,
        "expressions/Battery-low.gif": 182,
        "expressions/cloud-with-lighting.gif": 42,
        "expressions/leaves.gif": 55,
        "expressions/plant.gif": 165,
        "expressions/rain-cloud.gif": 98,
    }
    return frames_map.get(expression)
