/* Copree 官网 / 文档站交互：移动菜单、目录高亮、代码复制、文档筛选 */
(function () {
  'use strict'

  // 顶栏滚动阴影
  var header = document.querySelector('.site-header')
  var onScroll = function () {
    if (!header) return
    header.classList.toggle('scrolled', window.scrollY > 8)
  }
  window.addEventListener('scroll', onScroll, { passive: true })
  onScroll()

  // 移动端菜单
  var toggle = document.getElementById('menuToggle')
  var nav = document.getElementById('nav')
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open')
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false')
    })
    nav.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') nav.classList.remove('open')
    })
  }

  // 代码块复制按钮（文档正文 + 首页快速开始）
  document.querySelectorAll('.prose pre, pre.code').forEach(function (pre) {
    if (pre.querySelector('.copy-btn')) return
    var btn = document.createElement('button')
    btn.className = 'copy-btn'
    btn.type = 'button'
    btn.textContent = '复制'
    btn.addEventListener('click', function () {
      var code = pre.querySelector('code')
      var text = code ? code.innerText : pre.innerText
      var done = function () {
        btn.textContent = '已复制'
        setTimeout(function () { btn.textContent = '复制' }, 1500)
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {})
      } else {
        var ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        try { document.execCommand('copy'); done() } catch (e) { /* 忽略 */ }
        document.body.removeChild(ta)
      }
    })
    pre.appendChild(btn)
  })

  // 移动端文档导航折叠
  var sideToggle = document.getElementById('sideToggle')
  var sideInner = document.getElementById('sideInner')
  if (sideToggle && sideInner) {
    sideToggle.addEventListener('click', function () {
      var open = sideInner.classList.toggle('open')
      sideToggle.setAttribute('aria-expanded', open ? 'true' : 'false')
    })
  }

  // 回到顶部
  var toTop = document.createElement('button')
  toTop.type = 'button'
  toTop.className = 'to-top'
  toTop.setAttribute('aria-label', '回到顶部')
  toTop.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M18 15l-6-6-6 6"/></svg>'
  toTop.addEventListener('click', function () {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  })
  document.body.appendChild(toTop)
  window.addEventListener('scroll', function () {
    toTop.classList.toggle('show', window.scrollY > 900)
  }, { passive: true })

  // 截图随指针轻微倾斜：只在有精确指针的设备上跑，触屏和降低动效偏好一律不干预
  var finePointer = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (finePointer && !reduceMotion) {
    var MAX_TILT = 5
    Array.prototype.forEach.call(document.querySelectorAll('.gallery figure, .hero-shot'), function (fig) {
      var raf = 0
      var tilt = function (e) {
        if (raf) return
        raf = requestAnimationFrame(function () {
          raf = 0
          var r = fig.getBoundingClientRect()
          var px = (e.clientX - r.left) / r.width - 0.5
          var py = (e.clientY - r.top) / r.height - 0.5
          fig.style.setProperty('--rx', (-py * MAX_TILT * 2).toFixed(2) + 'deg')
          fig.style.setProperty('--ry', (px * MAX_TILT * 2).toFixed(2) + 'deg')
          fig.style.setProperty('--mx', ((px + 0.5) * 100).toFixed(1) + '%')
          fig.style.setProperty('--my', ((py + 0.5) * 100).toFixed(1) + '%')
        })
      }
      var reset = function () {
        if (raf) { cancelAnimationFrame(raf); raf = 0 }
        fig.style.setProperty('--rx', '0deg')
        fig.style.setProperty('--ry', '0deg')
      }
      fig.addEventListener('pointermove', tilt)
      fig.addEventListener('pointerleave', reset)
    })
  }

  // 首屏之外的内容滚动到接近视口时向上淡入；JS 失效或降低动效偏好时内容始终可见
  if (!reduceMotion && 'IntersectionObserver' in window) {
    var revealTargets = document.querySelectorAll(
      '.section-head, .cards-3 > *, .cards-4 > *, .gallery figure, .quickstart-grid > *, .fed-visual, .cta-band-inner > *'
    )
    var revealIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return
        entry.target.classList.add('reveal-in')
        revealIO.unobserve(entry.target)
      })
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 })

    var groups = new Map()
    Array.prototype.forEach.call(revealTargets, function (el) {
      // 首屏内的块不动，避免打开页面时整片元素闪一下
      if (el.getBoundingClientRect().top < window.innerHeight * 0.92) return
      var parent = el.parentElement
      var index = groups.get(parent) || 0
      groups.set(parent, index + 1)
      el.classList.add('reveal')
      el.style.transitionDelay = Math.min(index, 5) * 70 + 'ms'
      revealIO.observe(el)
    })
  }

  // 右侧目录滚动高亮
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll('.wiki-toc a'))
  if (tocLinks.length && 'IntersectionObserver' in window) {
    var map = {}
    tocLinks.forEach(function (a) {
      var id = decodeURIComponent(a.getAttribute('href').slice(1))
      if (id) map[id] = a
    })
    var headings = Array.prototype.slice.call(document.querySelectorAll('.prose h2[id]'))
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return
        tocLinks.forEach(function (a) { a.classList.remove('active') })
        var link = map[entry.target.id]
        if (link) link.classList.add('active')
      })
    }, { rootMargin: '-90px 0px -70% 0px' })
    headings.forEach(function (h) { io.observe(h) })
  }

  // 侧边栏文档筛选
  var search = document.getElementById('docSearch')
  if (search) {
    var links = Array.prototype.slice.call(document.querySelectorAll('.side-link'))
    var groups = Array.prototype.slice.call(document.querySelectorAll('.side-group'))
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase()
      links.forEach(function (a) {
        var hit = !q || (a.getAttribute('data-title') || '').toLowerCase().indexOf(q) >= 0
        a.style.display = hit ? '' : 'none'
      })
      groups.forEach(function (g) {
        var any = Array.prototype.some.call(g.querySelectorAll('.side-link'), function (a) {
          return a.style.display !== 'none'
        })
        g.style.display = any ? '' : 'none'
      })
    })
  }
})()
