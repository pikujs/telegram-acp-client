# Maintainer: pikujs <your-email@example.com>
pkgname=python-telegram-acp-client
_name=telegram-acp-client
pkgver=0.1.0
pkgrel=1
pkgdesc="Telegram bot to communicate with agents via ACP (Agent Client Protocol)"
arch=('any')
url="https://gitlab.pikujs.com/pikujs/telegram-acp-client"
license=('MIT')
depends=('python' 'python-agent-client-protocol' 'python-aiosqlite' 'python-httpx' 'python-python-dotenv' 'python-python-telegram-bot')
makedepends=('python-build' 'python-installer' 'python-hatchling' 'python-wheel')
optdepends=('systemd: for systemd service management')
source=("https://gitlab.pikujs.com/pikujs/telegram-acp-client/-/archive/v$pkgver/telegram-acp-client-v$pkgver.tar.gz"
        "telegram-acp-client@.service")
sha256sums=('SKIP' 'SKIP')

build() {
    cd "$_name-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "$_name-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 "../telegram-acp-client@.service" "$pkgdir/usr/lib/systemd/user/telegram-acp-client@.service"
}
