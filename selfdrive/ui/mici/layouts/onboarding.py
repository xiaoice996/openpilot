import numpy as np
import qrcode
import pyray as rl
from collections.abc import Callable
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.system.ui.mici_setup import GreyBigButton, BigPillButton
from openpilot.system.version import terms_version, training_version
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationCircleButton


class TrainingGuideAttentionNotice(Scroller):
  def __init__(self, continue_callback: Callable[[], None]):
    super().__init__()

    continue_button = BigPillButton("next")
    continue_button.set_click_callback(continue_callback)

    self._scroller.add_widgets([
      GreyBigButton("what is openpilot?", "scroll to continue",
                    gui_app.texture("icons_mici/setup/green_info.png", 64, 64)),
      GreyBigButton("", "1. openpilot is a driver assistance system."),
      GreyBigButton("", "2. You must pay attention at all times."),
      GreyBigButton("", "3. You must be ready to take over at any time."),
      GreyBigButton("", "4. You are fully responsible for driving the car."),
      continue_button,
    ])


class TrainingGuide(NavWidget):
  def __init__(self, completed_callback: Callable[[], None]):
    super().__init__()

    self._steps = [
      TrainingGuideAttentionNotice(continue_callback=completed_callback),
    ]

    self._child(self._steps[0])
    self._steps[0].set_enabled(lambda: self.enabled and not self.is_dismissing)  # for nav stack

  def _render(self, _):
    self._steps[0].render(self._rect)


class QRCodeWidget(Widget):
  def __init__(self, url: str, size: int = 170):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, size, size))
    self._size = size
    self._qr_texture: rl.Texture | None = None
    self._generate_qr(url)

  def _generate_qr(self, url: str):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=0)
    qr.add_data(url)
    qr.make(fit=True)

    pil_img = qr.make_image(fill_color="white", back_color="black").convert('RGBA')
    img_array = np.array(pil_img, dtype=np.uint8)

    rl_image = rl.Image()
    rl_image.data = rl.ffi.cast("void *", img_array.ctypes.data)
    rl_image.width = pil_img.width
    rl_image.height = pil_img.height
    rl_image.mipmaps = 1
    rl_image.format = rl.PixelFormat.PIXELFORMAT_UNCOMPRESSED_R8G8B8A8

    self._qr_texture = rl.load_texture_from_image(rl_image)

  def _render(self, _):
    if self._qr_texture:
      scale = self._size / self._qr_texture.height
      rl.draw_texture_ex(self._qr_texture, rl.Vector2(self._rect.x, self._rect.y), 0.0, scale, rl.WHITE)

  def __del__(self):
    if self._qr_texture and self._qr_texture.id != 0:
      rl.unload_texture(self._qr_texture)


class TermsPage(Scroller):
  def __init__(self, on_accept, on_decline):
    super().__init__()

    self._accept_button = BigConfirmationCircleButton("accept\nterms", gui_app.texture("icons_mici/setup/driver_monitoring/dm_check.png", 64, 64), on_accept)
    self._decline_button = BigConfirmationCircleButton("decline &\nuninstall", gui_app.texture("icons_mici/setup/cancel.png", 64, 64), on_decline,
                                                       red=True, exit_on_confirm=False)

    self._terms_header = GreyBigButton("terms and\nconditions", "scroll to continue",
                                       gui_app.texture("icons_mici/setup/green_info.png", 64, 64))
    self._must_accept_card = GreyBigButton("", "You must accept the Terms & Conditions to use openpilot.")

    self._scroller.add_widgets([
      self._terms_header,
      GreyBigButton("swipe for QR code", "or go to https://comma.ai/terms",
                    gui_app.texture("icons_mici/setup/small_slider/slider_arrow.png", 64, 56, flip_x=True)),
      QRCodeWidget("https://comma.ai/terms"),
      self._must_accept_card,
      self._accept_button,
      self._decline_button,
    ])

  def _render(self, _):
    rl.draw_rectangle_rec(self._rect, rl.BLACK)
    super()._render(_)


class OnboardingWindow(Widget):
  def __init__(self, completed_callback: Callable[[], None]):
    super().__init__()
    self._completed_callback = completed_callback
    self._accepted_terms: bool = ui_state.params.get("HasAcceptedTerms") == terms_version
    self._training_done: bool = ui_state.params.get("CompletedTrainingVersion") == training_version

    self.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

    # Windows
    self._terms = TermsPage(on_accept=self._on_terms_accepted, on_decline=self._on_uninstall)
    self._terms.set_enabled(lambda: self.enabled)  # for nav stack
    self._training_guide = TrainingGuide(completed_callback=self._on_completed_training)
    self._training_guide.set_enabled(lambda: self.enabled)  # for nav stack

  def _on_uninstall(self):
    ui_state.params.put_bool("DoUninstall", True)

  def show_event(self):
    super().show_event()
    device.set_override_interactive_timeout(300)
    device.set_offroad_brightness(100)

  def hide_event(self):
    super().hide_event()
    # FIXME: when nav stack sends hide event to widget 2 below on push, this needs to be moved
    device.set_override_interactive_timeout(None)
    device.set_offroad_brightness(None)

  @property
  def completed(self) -> bool:
    return self._accepted_terms and self._training_done

  def close(self):
    ui_state.params.put_bool_nonblocking("IsDriverViewEnabled", False)
    self._completed_callback()

  def _on_terms_accepted(self):
    ui_state.params.put("HasAcceptedTerms", terms_version)
    gui_app.push_widget(self._training_guide)

  def _on_completed_training(self):
    ui_state.params.put_bool_nonblocking("RecordFront", False)
    ui_state.params.put("CompletedTrainingVersion", training_version)
    self.close()

  def _render(self, _):
    rl.draw_rectangle_rec(self._rect, rl.BLACK)
    self._terms.render(self._rect)
