/**
 * Audio Processor - AudioWorklet
 * 
 * Runs in a separate thread for real-time audio processing.
 * Converts Float32 audio samples to Int16 PCM for streaming.
 * 
 * Protocol: Sends Int16 PCM (little-endian) per R3 specification.
 */

class AudioProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();

        // Configuration
        this.chunkSize = options?.processorOptions?.chunkSize || 4096;

        // Buffer for accumulating samples
        this.buffer = new Float32Array(this.chunkSize);
        this.bufferIndex = 0;

        // Level calculation
        this.levelSum = 0;
        this.levelCount = 0;
    }

    /**
     * Process audio frames
     * 
     * @param {Array<Float32Array[]>} inputs - Input audio channels
     * @param {Array<Float32Array[]>} outputs - Output audio channels  
     * @param {Object} parameters - Audio parameters
     * @returns {boolean} True to keep processing
     */
    process(inputs, outputs, parameters) {
        // Get first input, first channel (mono)
        const input = inputs[0];
        if (!input || input.length === 0) {
            return true;
        }

        const channel = input[0];
        if (!channel || channel.length === 0) {
            return true;
        }

        // Accumulate samples into buffer
        for (let i = 0; i < channel.length; i++) {
            const sample = channel[i];

            // Add to level calculation
            this.levelSum += sample * sample;
            this.levelCount++;

            // Add to buffer
            this.buffer[this.bufferIndex] = sample;
            this.bufferIndex++;

            // Check if buffer is full
            if (this.bufferIndex >= this.chunkSize) {
                this.sendChunk();
            }
        }

        return true;
    }

    /**
     * Send accumulated chunk to main thread
     */
    sendChunk() {
        // Calculate RMS level
        const rms = Math.sqrt(this.levelSum / this.levelCount);
        const level = Math.min(1, rms * 3); // Amplify and clamp

        // Convert Float32 to Int16 PCM (little-endian per R3)
        const int16Data = this.float32ToInt16(this.buffer);

        // Send to main thread
        this.port.postMessage({
            type: 'audio',
            data: int16Data,
            level: level
        });

        // Reset buffer
        this.bufferIndex = 0;
        this.levelSum = 0;
        this.levelCount = 0;
    }

    /**
     * Convert Float32 audio to Int16 PCM
     * 
     * @param {Float32Array} float32Array - Input samples (-1 to 1)
     * @returns {Int16Array} Output PCM samples
     */
    float32ToInt16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);

        for (let i = 0; i < float32Array.length; i++) {
            // Clamp to -1 to 1 range
            let sample = Math.max(-1, Math.min(1, float32Array[i]));

            // Convert to Int16 range (-32768 to 32767)
            int16Array[i] = sample < 0
                ? sample * 0x8000
                : sample * 0x7FFF;
        }

        return int16Array;
    }
}

// Register the processor
registerProcessor('audio-processor', AudioProcessor);
