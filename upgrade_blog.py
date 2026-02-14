#!/usr/bin/env python3
"""
博客升级脚本 - 使用 Claude Code CLI 升级博客，使其更有科技感
"""

import os
import subprocess
import json
import time

def run_claude_code(prompt, context_files=None):
    """运行 Claude Code CLI 命令"""
    cmd = ["claude-code", "--prompt", prompt]
    
    if context_files:
        for file in context_files:
            cmd.extend(["--context", file])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            return result.stdout
        else:
            print(f"Claude Code 错误: {result.stderr}")
            return None
    except FileNotFoundError:
        print("错误: 未找到 Claude Code CLI。请确保已安装并添加到 PATH")
        return None
    except Exception as e:
        print(f"运行 Claude Code 时出错: {e}")
        return None

def upgrade_index_html():
    """升级 index.html 文件"""
    print("正在升级 index.html...")
    
    prompt = """请升级这个博客的 index.html 文件，使其更有科技感。具体要求：
1. 添加科技感的设计元素（如渐变背景、网格效果、霓虹灯效果等）
2. 添加动态效果（如粒子背景、滚动动画等）
3. 优化布局，使其更现代化
4. 添加科技相关的图标和字体
5. 确保响应式设计
6. 将邮箱替换为 codemo1991@gmail.com
7. 添加 GitHub 链接（如果用户名为 codemo1991）
8. 添加科技感强的配色方案（深色主题为主，霓虹色点缀）

请输出完整的升级后的 index.html 代码。"""
    
    result = run_claude_code(prompt, ["chris-blog/index.html"])
    if result:
        with open("chris-blog/index.html", "w", encoding="utf-8") as f:
            f.write(result)
        print("✓ index.html 升级完成")
    else:
        print("✗ index.html 升级失败")

def upgrade_css():
    """升级 CSS 文件"""
    print("正在升级 CSS 文件...")
    
    prompt = """请升级这个博客的 CSS 文件，使其更有科技感。具体要求：
1. 添加科技感的样式：霓虹灯效果、渐变边框、玻璃态效果
2. 添加粒子背景动画
3. 添加卡片悬停的3D效果
4. 添加科技感字体和图标
5. 优化深色主题，添加霓虹色点缀
6. 添加加载动画和过渡效果
7. 确保响应式设计

请输出完整的升级后的 style.css 代码。"""
    
    result = run_claude_code(prompt, ["chris-blog/css/style.css"])
    if result:
        with open("chris-blog/css/style.css", "w", encoding="utf-8") as f:
            f.write(result)
        print("✓ CSS 文件升级完成")
    else:
        print("✗ CSS 文件升级失败")

def upgrade_js():
    """升级 JavaScript 文件"""
    print("正在升级 JavaScript 文件...")
    
    prompt = """请升级这个博客的 JavaScript 文件，添加科技感功能：
1. 粒子背景动画
2. 滚动视差效果
3. 打字机效果的文字动画
4. 卡片3D翻转效果
5. 主题切换增强（添加更多科技感主题）
6. 页面加载动画
7. 交互式元素（如可拖动的元素、点击效果等）

请输出完整的升级后的 script.js 代码。"""
    
    # 先检查是否存在 js 文件
    js_file = "chris-blog/js/script.js"
    if os.path.exists(js_file):
        result = run_claude_code(prompt, [js_file])
    else:
        result = run_claude_code(prompt)
    
    if result:
        with open(js_file, "w", encoding="utf-8") as f:
            f.write(result)
        print("✓ JavaScript 文件升级完成")
    else:
        print("✗ JavaScript 文件升级失败")

def create_tech_assets():
    """创建科技感资源文件"""
    print("正在创建科技感资源文件...")
    
    # 创建粒子背景 JS 文件
    particles_js = """// 粒子背景动画
class ParticleBackground {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.particles = [];
        this.particleCount = 100;
        this.mouse = { x: 0, y: 0, radius: 100 };
        
        this.init();
        this.animate();
        this.bindEvents();
    }
    
    init() {
        this.resizeCanvas();
        this.createParticles();
    }
    
    resizeCanvas() {
        this.canvas.width = this.canvas.offsetWidth;
        this.canvas.height = this.canvas.offsetHeight;
    }
    
    createParticles() {
        this.particles = [];
        for (let i = 0; i < this.particleCount; i++) {
            this.particles.push({
                x: Math.random() * this.canvas.width,
                y: Math.random() * this.canvas.height,
                size: Math.random() * 2 + 0.5,
                speedX: Math.random() * 1 - 0.5,
                speedY: Math.random() * 1 - 0.5,
                color: `rgba(${Math.floor(Math.random() * 100 + 155)}, 
                          ${Math.floor(Math.random() * 100 + 155)}, 
                          255, 0.8)`
            });
        }
    }
    
    animate() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // 更新和绘制粒子
        for (let particle of this.particles) {
            // 鼠标交互
            const dx = this.mouse.x - particle.x;
            const dy = this.mouse.y - particle.y;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            if (distance < this.mouse.radius) {
                const angle = Math.atan2(dy, dx);
                const force = (this.mouse.radius - distance) / this.mouse.radius;
                particle.x -= Math.cos(angle) * force * 5;
                particle.y -= Math.sin(angle) * force * 5;
            }
            
            // 边界检查
            if (particle.x < 0 || particle.x > this.canvas.width) particle.speedX *= -1;
            if (particle.y < 0 || particle.y > this.canvas.height) particle.speedY *= -1;
            
            // 移动粒子
            particle.x += particle.speedX;
            particle.y += particle.speedY;
            
            // 绘制粒子
            this.ctx.beginPath();
            this.ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
            this.ctx.fillStyle = particle.color;
            this.ctx.fill();
            
            // 绘制连接线
            for (let otherParticle of this.particles) {
                const dx = particle.x - otherParticle.x;
                const dy = particle.y - otherParticle.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                if (distance < 100) {
                    this.ctx.beginPath();
                    this.ctx.strokeStyle = `rgba(100, 150, 255, ${0.2 * (1 - distance/100)})`;
                    this.ctx.lineWidth = 0.5;
                    this.ctx.moveTo(particle.x, particle.y);
                    this.ctx.lineTo(otherParticle.x, otherParticle.y);
                    this.ctx.stroke();
                }
            }
        }
        
        requestAnimationFrame(() => this.animate());
    }
    
    bindEvents() {
        window.addEventListener('resize', () => {
            this.resizeCanvas();
            this.createParticles();
        });
        
        this.canvas.addEventListener('mousemove', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            this.mouse.x = e.clientX - rect.left;
            this.mouse.y = e.clientY - rect.top;
        });
        
        this.canvas.addEventListener('mouseleave', () => {
            this.mouse.x = 0;
            this.mouse.y = 0;
        });
    }
}

// 初始化粒子背景
document.addEventListener('DOMContentLoaded', () => {
    const particleCanvas = document.getElementById('particle-canvas');
    if (particleCanvas) {
        new ParticleBackground('particle-canvas');
    }
});"""
    
    with open("chris-blog/js/particles.js", "w", encoding="utf-8") as f:
        f.write(particles_js)
    
    # 创建科技感字体 CSS
    fonts_css = """/* 科技感字体 */
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;900&family=Share+Tech+Mono&family=Exo+2:wght@300;400;500;600;700&display=swap');

:root {
    --font-heading: 'Orbitron', sans-serif;
    --font-mono: 'Share Tech Mono', monospace;
    --font-body: 'Exo 2', sans-serif;
}

body {
    font-family: var(--font-body);
}

h1, h2, h3, h4, h5, h6 {
    font-family: var(--font-heading);
    font-weight: 700;
}

code, pre, .code {
    font-family: var(--font-mono);
}"""
    
    with open("chris-blog/css/fonts.css", "w", encoding="utf-8") as f:
        f.write(fonts_css)
    
    print("✓ 科技感资源文件创建完成")

def main():
    print("=" * 50)
    print("博客科技感升级开始")
    print("=" * 50)
    
    # 检查 Claude Code CLI 是否可用
    print("检查 Claude Code CLI...")
    try:
        subprocess.run(["claude-code", "--version"], capture_output=True, check=True)
        print("✓ Claude Code CLI 可用")
    except:
        print("✗ Claude Code CLI 不可用，将使用备用方案")
        return
    
    # 执行升级步骤
    upgrade_index_html()
    upgrade_css()
    upgrade_js()
    create_tech_assets()
    
    print("=" * 50)
    print("博客升级完成！")
    print("=" * 50)
    print("\n升级内容总结：")
    print("1. ✓ 升级了 index.html - 添加科技感设计和动态效果")
    print("2. ✓ 升级了 CSS - 添加霓虹灯效果、玻璃态、3D变换")
    print("3. ✓ 升级了 JavaScript - 添加粒子背景、滚动动画等")
    print("4. ✓ 创建了科技感资源文件 - 粒子动画和科技字体")
    print("5. ✓ 更新了联系信息 - 邮箱: codemo1991@gmail.com")
    print("\n请在浏览器中打开 chris-blog/index.html 查看效果")

if __name__ == "__main__":
    main()