document.addEventListener('DOMContentLoaded', () => {
    const ranges = document.querySelectorAll('input[type="range"]');
    for (const range of ranges) {
        const percentElement = document.createElement('div');
        percentElement.textContent = `${range.value}%`;
        percentElement.className = 'percent ms-3';
        range.insertAdjacentElement('afterend', percentElement);
        range.addEventListener('input', () => percentElement.innerHTML = `${range.value}%`);
    }
});