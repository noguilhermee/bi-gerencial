/**
 * Carrossel automático do dashboard gerencial Labor Rural.
 */
class DashboardCarousel {
  constructor(options = {}) {
    this.slideDuration = options.slideDuration || 30000;
    this.currentSlide = 0;
    this.slideNames = [
      'Visitas e cobertura',
      'Movimentações e vínculos',
      'Dados e consistência',
      'Econômico e Perfil dos Produtores',
      'Produção e Qualidade',
      'Carteira e Território',
      'Safra de Forrageiras'
    ];
    this.totalSlides = this.slideNames.length;
    this.track = document.getElementById('carouselTrack');
    this.track?.style.setProperty('--slides', String(this.totalSlides));
    this.slides = [...document.querySelectorAll('.slide')];
    this.dots = [...document.querySelectorAll('.page-dot')];
    this.title = document.getElementById('currentSlideTitle');
    this.progressBar = document.getElementById('progressBarFill');
    this.prevButton = document.getElementById('prevSlide');
    this.nextButton = document.getElementById('nextSlide');
    this.pauseButton = document.getElementById('carouselPauseToggle');
    this.navigation = document.querySelector('.carousel-navigation');
    this.startedAt = Date.now();
    this.progressTimer = null;
    this.hoverPaused = false;
    this.modalPaused = false;
    this.userPaused = options.startPaused !== undefined ? options.startPaused : true;
    window.dashboardCarousel = this;
    this.init();
  }

  pauseForModal(pause) {
    this.modalPaused = Boolean(pause);
    if (!this.modalPaused && !this.userPaused) {
      this.resetProgress();
    }
  }

  init() {
    if (!this.track) return;
    this.bindEvents();
    const hashPage = Number(window.location.hash.match(/pagina-(\d+)/)?.[1] || 1) - 1;
    this.goTo(Number.isInteger(hashPage) ? hashPage : 0, false);
    this.updatePauseUI();
    this.startProgress();
  }

  toggleUserPause() {
    this.userPaused = !this.userPaused;
    this.updatePauseUI();
    if (!this.userPaused) {
      this.resetProgress();
    }
  }

  updatePauseUI() {
    if (!this.pauseButton) return;
    const label = this.pauseButton.querySelector('.pause-toggle-label');
    const icon = this.pauseButton.querySelector('.pause-icon');
    
    if (this.userPaused) {
      this.pauseButton.classList.add('paused');
      this.pauseButton.setAttribute('aria-pressed', 'true');
      this.pauseButton.setAttribute('aria-label', 'Continuar rotação dos slides');
      if (label) label.textContent = 'Slide fixo';
      if (icon) icon.textContent = '▶';
    } else {
      this.pauseButton.classList.remove('paused');
      this.pauseButton.setAttribute('aria-pressed', 'false');
      this.pauseButton.setAttribute('aria-label', 'Pausar troca automática de slides');
      if (label) label.textContent = 'Pausar slides';
      if (icon) icon.textContent = '⏸';
    }
  }

  bindEvents() {
    this.pauseButton?.addEventListener('click', () => this.toggleUserPause());

    this.navigation?.addEventListener('click', (event) => {
      const pageButton = event.target.closest('[data-slide]');
      if (!pageButton) return;
      event.preventDefault();
      event.stopPropagation();
      this.goTo(Number(pageButton.dataset.slide));
    });

    this.prevButton?.addEventListener('click', () => this.goTo(this.currentSlide - 1));
    this.nextButton?.addEventListener('click', () => this.goTo(this.currentSlide + 1));

    window.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowRight') this.goTo(this.currentSlide + 1);
      if (event.key === 'ArrowLeft') this.goTo(this.currentSlide - 1);
      if (event.key === ' ' && event.target === document.body) {
        event.preventDefault();
        this.toggleUserPause();
      }
    });

    window.addEventListener('hashchange', () => {
      const hashPage = Number(window.location.hash.match(/pagina-(\d+)/)?.[1]);
      if (hashPage >= 1 && hashPage <= this.totalSlides) this.goTo(hashPage - 1);
    });

    const interactiveArea = document.querySelector('.carousel-viewport');
    interactiveArea?.addEventListener('mouseenter', () => { this.hoverPaused = true; });
    interactiveArea?.addEventListener('mouseleave', () => {
      this.hoverPaused = false;
      this.resetProgress();
    });
    interactiveArea?.addEventListener('focusin', () => { this.hoverPaused = true; });
    interactiveArea?.addEventListener('focusout', () => {
      this.hoverPaused = false;
      this.resetProgress();
    });
  }

  goTo(index, reset = true) {
    if (typeof window.closePanelFullscreen === 'function') {
      window.closePanelFullscreen();
    }
    const normalized = (index + this.totalSlides) % this.totalSlides;
    this.currentSlide = normalized;
    this.track.style.transform = `translate3d(-${normalized * 100}vw, 0, 0)`;

    this.dots.forEach((dot, dotIndex) => {
      const active = dotIndex === normalized;
      dot.classList.toggle('active', active);
      dot.setAttribute('aria-current', active ? 'page' : 'false');
    });

    this.slides.forEach((slide, slideIndex) => {
      slide.setAttribute('aria-hidden', slideIndex === normalized ? 'false' : 'true');
    });

    if (this.title) this.title.textContent = this.slideNames[normalized];
    window.history.replaceState(null, '', `#pagina-${normalized + 1}`);
    if (reset) this.resetProgress();
  }

  resetProgress() {
    this.startedAt = Date.now();
    if (this.progressBar && !this.userPaused) this.progressBar.style.width = '0%';
  }

  startProgress() {
    clearInterval(this.progressTimer);
    this.progressTimer = setInterval(() => {
      if (this.userPaused || this.hoverPaused || this.modalPaused) return;
      const elapsed = Date.now() - this.startedAt;
      const percent = Math.min(100, (elapsed / this.slideDuration) * 100);
      if (this.progressBar) this.progressBar.style.width = `${percent}%`;
      if (elapsed >= this.slideDuration) this.goTo(this.currentSlide + 1);
    }, 100);
  }
}

window.DashboardCarousel = DashboardCarousel;
