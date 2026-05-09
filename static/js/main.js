// Main JavaScript for MarketMinds
document.addEventListener('DOMContentLoaded', function() {
    // Mobile Navigation Toggle (single listener — base.html must not duplicate this)
    const navToggle = document.querySelector('.nav-toggle');
    const navMenu = document.querySelector('.nav-menu');

    function closeMobileNav() {
        if (!navMenu || !navToggle) return;
        navMenu.classList.remove('active');
        navToggle.classList.remove('active');
        navToggle.setAttribute('aria-expanded', 'false');
    }

    if (navToggle && navMenu) {
        function syncNavAria() {
            navToggle.setAttribute('aria-expanded', navMenu.classList.contains('active') ? 'true' : 'false');
        }
        navToggle.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            navMenu.classList.toggle('active');
            navToggle.classList.toggle('active');
            syncNavAria();
        });
        navToggle.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                navMenu.classList.toggle('active');
                navToggle.classList.toggle('active');
                syncNavAria();
            }
        });
        document.querySelectorAll('.nav-menu .nav-link, .nav-menu .nav-user').forEach(function (el) {
            el.addEventListener('click', closeMobileNav);
        });
        document.addEventListener('click', function (e) {
            if (!navMenu.classList.contains('active')) return;
            if (navToggle.contains(e.target)) return;
            if (navMenu.contains(e.target)) return;
            closeMobileNav();
        });
    }

    // Typing Effect for Hero Section
    const typedTextSpan = document.querySelector('.typed-text');
    if (typedTextSpan) {
        const textArray = ["AI-Powered Trading", "Market Analysis", "Investment Insights", "Real-Time Data"];
        const typingDelay = 100;
        const erasingDelay = 50;
        const newTextDelay = 2000;
        let textArrayIndex = 0;
        let charIndex = 0;

        function type() {
            if (charIndex < textArray[textArrayIndex].length) {
                typedTextSpan.textContent += textArray[textArrayIndex].charAt(charIndex);
                charIndex++;
                setTimeout(type, typingDelay);
            } else {
                setTimeout(erase, newTextDelay);
            }
        }

        function erase() {
            if (charIndex > 0) {
                typedTextSpan.textContent = textArray[textArrayIndex].substring(0, charIndex - 1);
                charIndex--;
                setTimeout(erase, erasingDelay);
            } else {
                textArrayIndex++;
                if (textArrayIndex >= textArray.length) textArrayIndex = 0;
                setTimeout(type, typingDelay + 1100);
            }
        }

        setTimeout(type, newTextDelay + 250);
    }

    // --- START MODIFICATION: Dynamic AI Picks Ticker ---
    const aiPicksTickerContent = document.getElementById('ai-picks-ticker-content');
    
    function fetchAndDisplayAIPicks() {
        if (!aiPicksTickerContent) return;

        // Clear existing content and set loading state
        aiPicksTickerContent.innerHTML = '<div class="ticker-item"><span class="symbol">Loading AI Picks...</span></div>';


        fetch('/api/ai_picks_ticker')
            .then(response => response.json())
            .then(data => {
                if (data.length > 0) {
                    let html = '';
                    
                    data.forEach(item => {
                        const isBuy = item.signal === 'BUY';
                        const changeClass = isBuy ? 'positive' : 'negative';
                        const signalIcon = isBuy ? 'fas fa-arrow-up' : 'fas fa-arrow-down';
                        
                        html += `
                            <div class="ticker-item ai-pick-item">
                                <span class="symbol ai-signal ${changeClass}">
                                    <i class="${signalIcon}"></i> ${item.signal}
                                </span>
                                <span class="symbol">${item.symbol}</span>
                                <span class="price">${item.price}</span>
                                <span class="change ${changeClass}">
                                    Score: ${item.score}
                                </span>
                                <span class="confidence-tag">
                                    Conf: ${item.confidence}
                                </span>
                            </div>
                        `;
                    });
                    
                    // Clone the content once for seamless scrolling
                    aiPicksTickerContent.innerHTML = html;
                    aiPicksTickerContent.innerHTML += html; 
                    
                } else {
                    aiPicksTickerContent.innerHTML = `
                        <div class="ticker-item">
                            <span class="symbol">AI-FAIL</span>
                            <span class="price">No strong signals currently.</span>
                        </div>
                    `;
                }
            })
            .catch(error => {
                console.error("Failed to fetch AI Picks:", error);
                aiPicksTickerContent.innerHTML = `
                    <div class="ticker-item">
                        <span class="symbol negative">ERROR</span>
                        <span class="price">Check Console/API.</span>
                    </div>
                `;
            });
    }

    fetchAndDisplayAIPicks();
    // Refresh every 60 seconds 
    setInterval(fetchAndDisplayAIPicks, 60000); 

    // --- END MODIFICATION ---

    // Search Functionality
    const searchInput = document.querySelector('.search-container input');
    const searchResults = document.querySelector('.search-results');
    
    if (searchInput && searchResults) {
        searchInput.addEventListener('focus', function() {
            searchResults.style.display = 'block';
        });
        
        searchInput.addEventListener('blur', function() {
            setTimeout(() => {
                searchResults.style.display = 'none';
            }, 200);
        });
        
        searchInput.addEventListener('input', function() {
            // Placeholder/mock search logic (replace with your actual /api/search implementation if needed)
            const query = this.value.toLowerCase();
            if (query.length > 0) {
                const results = [
                    { symbol: 'AAPL', name: 'Apple Inc.', price: 182.52, change: 1.24 },
                    { symbol: 'MSFT', name: 'Microsoft Corp.', price: 407.81, change: -0.67 },
                    { symbol: 'GOOGL', name: 'Alphabet Inc.', price: 173.69, change: 2.31 },
                ].filter(stock => 
                    stock.symbol.toLowerCase().includes(query) || 
                    stock.name.toLowerCase().includes(query)
                );
                
                displaySearchResults(results);
            } else {
                searchResults.innerHTML = '';
            }
        });
        
        function displaySearchResults(results) {
            searchResults.innerHTML = '';
            results.forEach(stock => {
                const resultElement = document.createElement('div');
                resultElement.className = 'search-result';
                resultElement.innerHTML = `
                    <div>
                        <div class="stock-symbol">${stock.symbol}</div>
                        <div class="stock-name">${stock.name}</div>
                    </div>
                    <div>
                        <div class="stock-price">$${stock.price.toFixed(2)}</div>
                        <div class="stock-change ${stock.change >= 0 ? 'positive' : 'negative'}">
                            ${stock.change >= 0 ? '+' : ''}${stock.change.toFixed(2)}%
                        </div>
                    </div>
                `;
                resultElement.addEventListener('click', function() {
                    window.location.href = `/stock/${stock.symbol}`;
                });
                searchResults.appendChild(resultElement);
            });
        }
    }

    // News Filter Tabs
    const filterBtns = document.querySelectorAll('.filter-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            filterBtns.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            
            const filter = this.getAttribute('data-filter');
            filterNewsItems(filter);
        });
    });
    
    function filterNewsItems(filter) {
        const newsItems = document.querySelectorAll('.news-item');
        newsItems.forEach(item => {
            if (filter === 'all' || item.getAttribute('data-category') === filter) {
                item.style.display = 'block';
            } else {
                item.style.display = 'none';
            }
        });
    }

    // Animate elements on scroll
    const animateOnScroll = function() {
        const elements = document.querySelectorAll('.feature-card, .news-card, .tech-item');
        
        elements.forEach(element => {
            const elementPosition = element.getBoundingClientRect().top;
            const screenPosition = window.innerHeight / 1.2;
            
            if (elementPosition < screenPosition) {
                element.style.opacity = '1';
                element.style.transform = 'translateY(0)';
            }
        });
    };
    
    // Set initial state for animation
    const animatedElements = document.querySelectorAll('.feature-card, .news-card, .tech-item');
    animatedElements.forEach(element => {
        element.style.opacity = '0';
        element.style.transform = 'translateY(20px)';
        element.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    });
    
    window.addEventListener('scroll', animateOnScroll);
    animateOnScroll();

    // Portfolio Chart Animation
    const chartContainer = document.querySelector('.chart-container');
    if (chartContainer) {
        const chart = document.createElement('div');
        chart.className = 'chart-line';
        chart.style.position = 'absolute';
        chart.style.bottom = '0';
        chart.style.left = '0';
        chart.style.width = '100%';
        chart.style.height = '2px';
        chart.style.background = 'var(--primary-gold)';
        chart.style.borderRadius = '1px';
        chart.style.transformOrigin = 'left';
        chart.style.animation = 'chart-grow 2s ease-out';
        
        chartContainer.appendChild(chart);
    }

    // Sentiment Meter Animation
    const sentimentMeters = document.querySelectorAll('.meter-fill');
    sentimentMeters.forEach(meter => {
        const percentage = meter.getAttribute('data-percentage') || '75';
        meter.style.width = `${percentage}%`;
    });

    // Flash Messages Auto-hide
    const flashMessages = document.querySelectorAll('.flash-message');
    flashMessages.forEach(message => {
        setTimeout(() => {
            message.style.opacity = '0';
            message.style.transform = 'translateX(100%)';
            setTimeout(() => {
                message.remove();
            }, 300);
        }, 5000);
    });

    // Smooth Scrolling for Anchor Links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });

    // Form Validation
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const inputs = this.querySelectorAll('input[required]');
            let valid = true;
            
            inputs.forEach(input => {
                if (!input.value.trim()) {
                    valid = false;
                    input.style.borderColor = 'var(--negative)';
                } else {
                    input.style.borderColor = 'rgba(255, 215, 0, 0.2)';
                }
            });
            
            if (!valid) {
                e.preventDefault();
                const errorMsg = document.createElement('div');
                errorMsg.className = 'flash-message';
                errorMsg.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please fill in all required fields.';
                document.body.appendChild(errorMsg);
                
                setTimeout(() => {
                    errorMsg.remove();
                }, 5000);
            }
        });
    });

    // Initialize tooltips
    const tooltipElements = document.querySelectorAll('[data-tooltip]');
    tooltipElements.forEach(element => {
        element.addEventListener('mouseenter', function() {
            const tooltip = document.createElement('div');
            tooltip.className = 'tooltip';
            tooltip.textContent = this.getAttribute('data-tooltip');
            document.body.appendChild(tooltip);
            
            const rect = this.getBoundingClientRect();
            tooltip.style.position = 'fixed';
            tooltip.style.left = rect.left + 'px';
            tooltip.style.top = (rect.top - tooltip.offsetHeight - 10) + 'px';
            tooltip.style.background = 'var(--card-bg)';
            tooltip.style.color = 'var(--text-primary)';
            tooltip.style.padding = '0.5rem 1rem';
            tooltip.style.borderRadius = '6px';
            tooltip.style.fontSize = '0.8rem';
            tooltip.style.zIndex = '10000';
            tooltip.style.border = '1px solid rgba(255, 215, 0, 0.2)';
            tooltip.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.3)';
            
            this.tooltip = tooltip;
        });
        
        element.addEventListener('mouseleave', function() {
            if (this.tooltip) {
                this.tooltip.remove();
                this.tooltip = null;
            }
        });
    });

    console.log('MarketMinds initialized successfully');
});