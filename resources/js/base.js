document.addEventListener('DOMContentLoaded', () => {
    // Check for minimal mode
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('minimal') === 'true') {
        document.body.setAttribute('data-minimal', 'true');
    }

    /* DEPRECATED: Slider value displays are now handled by:
     * - audioSource.js setupSlider() for index.html sliders (with proper units like "s")
     * - settings.html inline slider-input elements (editable number inputs)
     * This code created duplicate displays and used incorrect "%" suffix for all values.
    // Initialize range sliders to show the current value
    const ranges = document.querySelectorAll('input[type="range"]');
    for (const range of ranges) {
        const percentElement = document.createElement('div');
        percentElement.textContent = `${range.value}`;
        percentElement.className = 'percent ms-3';
        range.insertAdjacentElement('afterend', percentElement);
        range.addEventListener('input', () => percentElement.innerHTML = `${range.value}`);
    }
    */

    // Initialize doNotFollowLinks
    const doNotFollowLinks = document.querySelectorAll("a[data-do-not-follow-link]");
    for (const link of doNotFollowLinks) {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            fetch(link.href);
        });
    }

    // Initialize tooltips (only if not in minimal mode)
    if (!document.body.hasAttribute('data-minimal')) {
        const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]')
        const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => 
            new bootstrap.Tooltip(tooltipTriggerEl))
    }

    // Optimize for embedded view if in minimal mode
    if (document.body.hasAttribute('data-minimal')) {
        // Remove unnecessary elements
        const elementsToRemove = document.querySelectorAll('.bottom-nav, #header, #footer');
        elementsToRemove.forEach(el => el.remove());

        // Optimize container
        const lyricsContainer = document.getElementById('lyrics');
        if (lyricsContainer) {
            lyricsContainer.style.minHeight = '100vh';
            lyricsContainer.style.padding = '0';
        }

        // Prevent right-click menu in embedded view
        document.addEventListener('contextmenu', (e) => e.preventDefault());
    }
});