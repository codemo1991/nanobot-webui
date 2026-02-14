#!/usr/bin/env python3
"""
简单的博客升级脚本
"""

import os

def upgrade_index_html():
    """升级 index.html 文件"""
    print("正在升级 index.html...")
    
    # 读取原始文件
    with open("chris-blog/index.html", "r", encoding="utf-8") as f:
        content = f.read()
    
    # 替换邮箱
    content = content.replace("your.email@example.com", "codemo1991@gmail.com")
    content = content.replace("yourusername", "codemo1991")
    
    # 添加科技感 CSS 链接
    head_insert = """    <!-- 科技感升级 -->
    <link rel="stylesheet" href="css/tech.css">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;900&family=Share+Tech+Mono&family=Exo+2:wght@300;400;500;600;700&display=swap">
    <script src="js/tech.js" defer></script>"""
    
    # 在 head 标签结束前插入
    if '</head>' in content:
        content = content.replace('</head>', f'{head_insert}\n</head>')
    
    # 在 body 开始后添加粒子画布
    if '<body>' in content:
        content = content.replace('<body>', '<body>\n    <!-- 粒子背景 -->\n    <canvas id="particle-canvas"></canvas>')
    
    # 更新英雄区域
    hero_section = """        <section class="hero">
            <div class="hero-content">
                <h1 class="tech-title">
                    <span class="tech-gradient">CHRIS</span>
                    <span class="tech-subtitle">TECH BLOG</span>
                </h1>
                <p class="tech-description">探索 AI、Web开发与前沿技术</p>
                <div class="tech-stats">
                    <div class="stat">
                        <span class="stat-number" data-count="100">0</span>
                        <span class="stat-label">文章</span>
                    </div>
                    <div class="stat">
                        <span class="stat-number" data-count="50">0</span>
                        <span class="stat-label">项目</span>
                    </div>
                    <div class="stat">
                        <span class="stat-number" data-count="1000">0</span>
                        <span class="stat-label">读者</span>
                    </div>
                </div>
                <a href="#recent-posts" class="tech-btn">
                    <span>探索内容</span>
                    <i class="fas fa-arrow-right"></i>
                </a>
            </div>
            <div class="hero-grid">
                <div class="grid-line"></div>
                <div class="grid-line"></div>
                <div class="grid-line"></div>
            </div>
        </section>"""
    
    # 替换旧的 hero 部分
    old_hero_start = '<section class="hero">'
    old_hero_end = '</section>'
    if old_hero_start in content and old_hero_end in content:
        start_idx = content.find(old_hero_start)
        end_idx = content.find(old_hero_end, start_idx) + len(old_hero_end)
        content = content[:start_idx] + hero_section + content[end_idx:]
    
    # 写入升级后的文件
    with open("chris-blog/index.html", "w", encoding="utf-8") as f:
        f.write(content)
    
    print("✓ index.html 升级完成")

def create_tech_css():
    """创建科技感 CSS"""
    print("正在创建科技感 CSS...")
    
    tech_css = """/* 科技感样式 */
:root {
    /* 科技感配色 */
    --tech-primary: #00d4ff;
    --tech-secondary: #ff00ff;
    --tech-accent: #00ff9d;
    --tech-dark: #0a0a1a;
    --tech-darker: #050510;
    --tech-light: #f0f8ff;
    
    /* 霓虹效果 */
    --neon-glow: 0 0 10px var(--tech-primary),
                 0 0 20px var(--tech-primary),
                 0 0 30px var(--tech-primary);
    
    /* 网格背景 */
    --grid-color: rgba(0, 212, 255, 0.1);
}

/* 粒子画布 */
#particle-canvas {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: -1;
    pointer-events: none;
}

/* 科技感字体 */
.tech-title {
    font-family: 'Orbitron', sans-serif;
    font-size: 4rem;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 1rem;
}

.tech-gradient {
    background: linear-gradient(45deg, var(--tech-primary), var(--tech-secondary), var(--tech-accent));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    animation: gradient-shift 3s ease infinite;
}

.tech-subtitle {
    display: block;
    font-size: 1.5rem;
    font-weight: 500;
    color: var(--tech-light);
    opacity: 0.8;
    margin-top: 0.5rem;
}

.tech-description {
    font-family: 'Exo 2', sans-serif;
    font-size: 1.2rem;
    color: var(--tech-light);
    opacity: 0.9;
    margin-bottom: 2rem;
}

/* 科技感按钮 */
.tech-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 1rem 2rem;
    background: linear-gradient(45deg, var(--tech-primary), var(--tech-secondary));
    color: white;
    text-decoration: none;
    border-radius: 50px;
    font-family: 'Orbitron', sans-serif;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border: none;
    cursor: pointer;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
    z-index: 1;
}

.tech-btn::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(45deg, var(--tech-secondary), var(--tech-primary));
    transition: left 0.3s ease;
    z-index: -1;
}

.tech-btn:hover::before {
    left: 0;
}

.tech-btn:hover {
    transform: translateY(-2px);
    box-shadow: var(--neon-glow);
}

/* 统计数字 */
.tech-stats {
    display: flex;
    gap: 3rem;
    margin: 2rem 0;
    justify-content: center;
}

.stat {
    text-align: center;
}

.stat-number {
    display: block;
    font-family: 'Orbitron', sans-serif;
    font-size: 2.5rem;
    font-weight: 700;
    color: var(--tech-primary);
    text-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
}

.stat-label {
    display: block;
    font-family: 'Exo 2', sans-serif;
    font-size: 0.9rem;
    color: var(--tech-light);
    opacity: 0.8;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.5rem;
}

/* 网格背景 */
.hero-grid {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: -1;
    opacity: 0.3;
}

.grid-line {
    position: absolute;
    background: linear-gradient(90deg, transparent, var(--tech-primary), transparent);
    height: 1px;
    width: 100%;
}

.grid-line:nth-child(1) { top: 25%; }
.grid-line:nth-child(2) { top: 50%; }
.grid-line:nth-child(3) { top: 75%; }

/* 卡片科技感升级 */
.post-card {
    background: rgba(10, 10, 26, 0.8);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(0, 212, 255, 0.2);
    border-radius: 15px;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}

.post-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: linear-gradient(45deg, transparent, rgba(0, 212, 255, 0.1), transparent);
    transform: translateX(-100%);
    transition: transform 0.6s ease;
}

.post-card:hover::before {
    transform: translateX(100%);
}

.post-card:hover {
    transform: translateY(-5px) scale(1.02);
    border-color: var(--tech-primary);
    box-shadow: 0 10px 30px rgba(0, 212, 255, 0.2),
                inset 0 0 20px rgba(0, 212, 255, 0.1);
}

/* 标签科技感 */
.tag {
    background: linear-gradient(45deg, var(--tech-primary), var(--tech-accent));
    color: var(--tech-darker);
    font-weight: 600;
    border-radius: 20px;
    padding: 0.3rem 1rem;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* 动画 */
@keyframes gradient-shift {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
}

@keyframes count-up {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}

/* 响应式设计 */
@media (max-width: 768px) {
    .tech-title {
        font-size: 2.5rem;
    }
    
    .tech-stats {
        flex-direction: column;
        gap: 1.5rem;
    }
    
    .stat-number {
        font-size: 2rem;
    }
}

/* 深色主题增强 */
[data-theme="dark"] {
    --tech-primary: #00d4ff;
    --tech-secondary: #ff00ff;
    --tech-accent: #00ff9d;
    --tech-dark: #050510;
    --tech-darker: #020208;
    --tech-light: #ffffff;
}

/* 滚动动画 */
.reveal {
    opacity: 0;
    transform: translateY(30px);
    transition: all 0.6s ease;
}

.reveal.active {
    opacity: 1;
    transform: translateY(0);
}"""
    
    with open("chris-blog/css/tech.css", "w", encoding="utf-8") as f:
        f.write(tech_css)
    
    print("✓ 科技感 CSS 创建完成")

def create_tech_js():
    """创建科技感 JavaScript"""
    print("正在创建科技感 JavaScript...")
    
    tech_js = """// 科技感功能
class TechBlog {
    constructor() {
        this.init();
    }
    
    init() {
        this.initParticles();
        this.initCounters();
        this.initScrollReveal();
    }
    
    initParticles() {
        const canvas = document.getElementById('particle-canvas');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        let particles = [];
        const particleCount = 80;
        
        // 设置画布尺寸
        function resizeCanvas() {
            canvas.width = canvas.offsetWidth;
            canvas.height = canvas.offsetHeight;
        }
        
        // 创建粒子
        function createParticles() {
            particles = [];
            for (let i = 0; i < particleCount; i++) {
                particles.push({
                    x: Math.random() * canvas.width,
                    y: Math.random() * canvas.height,
                    size: Math.random() * 2 + 0.5,
                    speedX: Math.random() * 1 - 0.5,
                    speedY: Math.random() * 1 - 0.5,
                    color: `rgba(${Math.floor(Math.random() * 100 + 155)}, 
                              ${Math.floor(Math.random() * 100 + 155)}, 
                              255, 0.6)`
                });
            }
        }
        
        // 动画循环
        function animate() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // 更新和绘制粒子
            particles.forEach(particle => {
                // 边界检查
                if (particle.x < 0 || particle.x > canvas.width) particle.speedX *= -1;
                if (particle.y < 0 || particle.y > canvas.height) particle.speedY *= -1;
                
                // 移动粒子
                particle.x += particle.speedX;
                particle.y += particle.speedY;
                
                // 绘制粒子
                ctx.beginPath();
                ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
                ctx.fillStyle = particle.color;
                ctx.fill();
                
                // 绘制连接线
                particles.forEach(otherParticle => {
                    const dx = particle.x - otherParticle.x;
                    const dy = particle.y - otherParticle.y;
                    const distance = Math.sqrt(dx * dx + dy * dy);
                    
                    if (distance < 100) {
                        ctx.beginPath();
                        ctx.strokeStyle = `rgba(100, 150, 255, ${0.2 * (1 - distance/100)})`;
                        ctx.lineWidth = 0.5;
                        ctx.moveTo(particle.x, particle.y);
                        ctx.lineTo(otherParticle.x, otherParticle.y);
                        ctx.stroke();
                    }
                });
            });
            
            requestAnimationFrame(animate);
        }
        
        // 初始化
        resizeCanvas();
        createParticles();
        animate();
        
        // 窗口大小改变时重置
        window.addEventListener('resize', () => {
            resizeCanvas();
            createParticles();
        });
    }
    
    initCounters() {
        const counters = document.querySelectorAll('.stat-number');
        if (!counters.length) return;
        
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const counter = entry.target;
                    const target = parseInt(counter.getAttribute('data-count'));
                    const duration = 2000; // 2秒
                    const step = target / (duration / 16); // 60fps
                    let current = 0;
                    
                    const updateCounter = () => {
                        current += step;
                        if (current < target) {
                            counter.textContent = Math.floor(current);
                            requestAnimationFrame(updateCounter);
                        } else {
                            counter.textContent = target;
                        }
                    };
                    
                    updateCounter();
                    observer.unobserve(counter);
                }
            });
        }, { threshold: 0.5 });
        
        counters.forEach(counter => observer.observe(counter));
    }
    
    initScrollReveal() {
        const reveals = document.querySelectorAll('.reveal');
        if (!reveals.length) return;
        
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('active');
                }
            });
        }, { threshold: 0.1 });
        
        reveals.forEach(reveal => observer.observe(reveal));
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    new TechBlog();
});"""
    
    with open("chris-blog/js/tech.js", "w", encoding="utf-8") as f:
        f.write(tech_js)
    
    print("✓ 科技感 JavaScript 创建完成")

def main():
    print("=" * 50)
    print("博客科技感升级开始")
    print("=" * 50)
    
    # 执行升级步骤
    upgrade_index_html()
    create_tech_css()
    create_tech_js()
    
    print("=" * 50)
    print("博客升级完成！")
    print("=" * 50)
    print("\n升级内容总结：")
    print("1. ✓ 升级了 index.html - 添加科技感设计和动态效果")
    print("2. ✓ 创建了 tech.css - 添加霓虹灯效果、玻璃态、3D变换")
    print("3. ✓ 创建了 tech.js - 添加粒子背景、滚动动画、计数器")
    print("4. ✓ 更新了联系信息 - 邮箱: codemo1991@gmail.com")
    print("\n请在浏览器中打开 chris-blog/index.html 查看效果")

if __name__ == "__main__":
    main()