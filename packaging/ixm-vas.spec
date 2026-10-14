# PyInstaller spec: two executables sharing one program folder.
#
#   IXM-VAS.exe         the window, built without a console
#   ixm-vas-worker.exe  task processes; a console program the window starts hidden
#
# A task reports to the window over its stdout pipe (ixm_vas/protocol.py), and
# a windowed executable has no stdout to give it -- hence the second exe.
# Playwright's driver comes along through Playwright's own PyInstaller hook;
# no browser is bundled, the tasks drive the VA's installed Google Chrome.
#
# Build with packaging/build.ps1, not by hand.

from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "assets" / "ixm-vas.ico")
DATAS = [(str(ROOT / "VERSION"), "."), (ICON, "assets")]

window = Analysis([str(ROOT / "IXM-VAS.pyw")], pathex=[str(ROOT)], datas=DATAS)
worker = Analysis([str(ROOT / "ixm_vas" / "worker.py")], pathex=[str(ROOT)], datas=DATAS)

window_exe = EXE(
    PYZ(window.pure), window.scripts, [],
    exclude_binaries=True, name="IXM-VAS", icon=ICON, console=False, upx=False,
)
worker_exe = EXE(
    PYZ(worker.pure), worker.scripts, [],
    exclude_binaries=True, name="ixm-vas-worker", icon=ICON, console=True, upx=False,
)

COLLECT(
    window_exe, window.binaries, window.datas,
    worker_exe, worker.binaries, worker.datas,
    name="IXM-VAS", upx=False,
)
