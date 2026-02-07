// ナビバー関連のJavaScript

document.addEventListener('DOMContentLoaded', function() {
  // ナビバー切り替え時のアニメーション
  const navbarToggler = document.querySelector('.navbar-toggler');
  const navbarCollapse = document.querySelector('.navbar-collapse');

  if (navbarToggler) {
    navbarToggler.addEventListener('click', function() {
      // アクティブ状態の管理（任意）
    });
  }

  // モバイルメニューをクリック時に自動で閉じる
  const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
  navLinks.forEach(link => {
    link.addEventListener('click', function() {
      if (navbarCollapse && navbarCollapse.classList.contains('show')) {
        navbarToggler.click();
      }
    });
  });
});
