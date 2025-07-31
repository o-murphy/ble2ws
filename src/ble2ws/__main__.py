from pathlib import Path
from ble2ws import server
import webbrowser


def demo():
    print("Demo server/client")
    index_file = Path(__file__).parent / "index.html"
    webbrowser.open(index_file.resolve().as_posix())
    server.main()


if __name__ == "__main__":
    demo()
