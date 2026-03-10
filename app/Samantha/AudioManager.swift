import AVFoundation
import AppKit
import os

private let log = Logger(subsystem: "com.samantha.app", category: "AudioManager")

enum AudioConstants {
    static let sampleRate: Double = 24000
    static let captureBufferSize: AVAudioFrameCount = 1024
    static let channels: AVAudioChannelCount = 1
}

@MainActor
final class AudioManager: ObservableObject {
    @Published private(set) var isCapturing = false
    @Published private(set) var isPlaying = false
    @Published private(set) var permissionGranted = false

    private var audioEngine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private var mixerNode: AVAudioMixerNode?
    private let audioQueue = DispatchQueue(label: "com.samantha.audio-capture")
    private var capturing = false // audioQueue-only flag

    private var playerNode: AVAudioPlayerNode?
    private let playbackQueue = DispatchQueue(label: "com.samantha.audio-playback")
    private var playbackEpoch: UInt64 = 0 // incremented on stop to discard stale callbacks
    private var samplesScheduled: UInt64 = 0
    private var samplesPlayed: UInt64 = 0

    // MARK: - Permission

    func requestPermission() async -> Bool {
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        switch status {
        case .authorized:
            permissionGranted = true
            return true
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            permissionGranted = granted
            return granted
        case .denied, .restricted:
            permissionGranted = false
            return false
        @unknown default:
            permissionGranted = false
            return false
        }
    }

    // MARK: - Capture

    func startCapture(onAudioData: @escaping @Sendable (Data) -> Void) {
        guard !isCapturing else { return }

        audioQueue.async { [weak self] in
            guard let self else { return }
            do {
                try self.setupAndStart(onChunk: onAudioData)
            } catch {
                log.error("Capture start failed: \(error.localizedDescription)")
                DispatchQueue.main.async { self.isCapturing = false }
            }
        }
    }

    func stopCapture() {
        playbackQueue.async { [weak self] in
            guard let self else { return }
            self.playbackEpoch &+= 1
            self.samplesScheduled = 0
            self.samplesPlayed = 0
        }
        audioQueue.async { [weak self] in
            guard let self else { return }
            self.capturing = false
            self.teardown()
        }
        isCapturing = false
        isPlaying = false
    }

    // MARK: - Playback

    /// Enqueue PCM16 24kHz mono audio bytes for immediate playback.
    /// Lazily attaches a player node to the engine if needed.
    func enqueueAudio(data: Data) {
        playbackQueue.async { [weak self] in
            guard let self else { return }

            do {
                try self.ensurePlayerReady()
            } catch {
                log.error("Playback engine setup failed: \(error.localizedDescription)")
                return
            }

            guard let player = self.playerNode,
                  let buffer = self.pcm16DataToBuffer(data) else { return }

            let epoch = self.playbackEpoch
            let scheduled = UInt64(buffer.frameLength)
            self.samplesScheduled += scheduled

            let wasIdle = self.samplesPlayed >= (self.samplesScheduled - scheduled)
            if wasIdle {
                DispatchQueue.main.async { self.isPlaying = true }
            }

            player.scheduleBuffer(buffer) { [weak self] in
                self?.playbackQueue.async {
                    guard let self, self.playbackEpoch == epoch else { return }
                    self.samplesPlayed += scheduled
                    if self.samplesPlayed >= self.samplesScheduled {
                        DispatchQueue.main.async { self.isPlaying = false }
                    }
                }
            }
        }
    }

    /// Immediately halt playback and discard all scheduled buffers.
    func stopPlayback() {
        playbackQueue.async { [weak self] in
            guard let self else { return }
            self.playbackEpoch &+= 1
            self.samplesScheduled = 0
            self.samplesPlayed = 0
            self.playerNode?.stop()
            DispatchQueue.main.async { self.isPlaying = false }
            log.debug("Playback stopped (epoch \(self.playbackEpoch))")
        }
    }

    /// Attach and connect playerNode to the shared engine. Starts engine if not running.
    /// Re-calls play() if the node exists but was stopped.
    private func ensurePlayerReady() throws {
        if let player = playerNode {
            if !player.isPlaying { player.play() }
            return
        }

        let engine: AVAudioEngine
        if let existing = self.audioEngine {
            engine = existing
        } else {
            engine = AVAudioEngine()
            self.audioEngine = engine
        }

        let player = AVAudioPlayerNode()
        engine.attach(player)

        let playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: AudioConstants.sampleRate,
            channels: AudioConstants.channels,
            interleaved: true
        )!
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)

        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }

        player.play()
        self.playerNode = player
        log.info("Playback player node attached and playing")
    }

    private func pcm16DataToBuffer(_ data: Data) -> AVAudioPCMBuffer? {
        let bytesPerSample = 2
        let frameCount = AVAudioFrameCount(data.count / bytesPerSample)
        guard frameCount > 0 else { return nil }

        let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: AudioConstants.sampleRate,
            channels: AudioConstants.channels,
            interleaved: true
        )!
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            return nil
        }
        buffer.frameLength = frameCount

        data.withUnsafeBytes { raw in
            guard let src = raw.baseAddress else { return }
            memcpy(buffer.int16ChannelData![0], src, data.count)
        }
        return buffer
    }

    // MARK: - Engine Setup (audioQueue)

    private func setupAndStart(onChunk: @escaping @Sendable (Data) -> Void) throws {
        teardown()

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)

        guard inputFormat.channelCount > 0, inputFormat.sampleRate > 0 else {
            throw AudioCaptureError.invalidInputFormat
        }

        let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: AudioConstants.sampleRate,
            channels: AudioConstants.channels,
            interleaved: false
        )!

        // Mixer handles multi-channel -> mono downmix and provides a stable tap point.
        let mixer = AVAudioMixerNode()
        engine.attach(mixer)

        // Determine capture format: downmix multi-channel inputs to mono for
        // consistent pipeline behavior across mic hardware (e.g. MacBook Pro 9-ch array).
        let captureFormat: AVAudioFormat
        if inputFormat.channelCount > 1 {
            captureFormat = AVAudioFormat(
                standardFormatWithSampleRate: inputFormat.sampleRate,
                channels: 1
            ) ?? inputFormat
        } else {
            captureFormat = inputFormat
        }

        mixer.volume = 1.0
        engine.connect(input, to: mixer, format: captureFormat)
        engine.prepare()

        // Build converter if device format differs from target.
        var audioConverter: AVAudioConverter?
        if captureFormat != targetFormat {
            audioConverter = AVAudioConverter(from: captureFormat, to: targetFormat)
            guard audioConverter != nil else {
                throw AudioCaptureError.converterCreationFailed
            }
        }

        mixer.installTap(
            onBus: 0,
            bufferSize: AudioConstants.captureBufferSize,
            format: captureFormat
        ) { [weak self] buffer, _ in
            self?.audioQueue.async { [weak self] in
                guard let self, self.capturing else { return }

                let floatBuffer: AVAudioPCMBuffer
                if let conv = audioConverter {
                    guard let converted = self.convert(buffer: buffer, using: conv, to: targetFormat) else {
                        return
                    }
                    floatBuffer = converted
                } else {
                    floatBuffer = buffer
                }

                guard let pcmData = self.float32ToPCM16Data(floatBuffer) else { return }
                onChunk(pcmData)
            }
        }

        try engine.start()

        self.audioEngine = engine
        self.mixerNode = mixer
        self.converter = audioConverter
        self.capturing = true
        DispatchQueue.main.async { self.isCapturing = true }

        log.info("Capture started: \(inputFormat.channelCount)ch \(Int(inputFormat.sampleRate))Hz -> 24kHz PCM16 mono")
    }

    private func teardown() {
        playerNode?.stop()
        playerNode = nil
        audioEngine?.stop()
        mixerNode?.removeTap(onBus: 0)
        audioEngine = nil
        mixerNode = nil
        converter = nil
    }

    // MARK: - Format Conversion (audioQueue)

    private func convert(
        buffer: AVAudioPCMBuffer,
        using converter: AVAudioConverter,
        to targetFormat: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        let ratio = Float(targetFormat.sampleRate) / Float(buffer.format.sampleRate)
        let frameCount = AVAudioFrameCount(Float(buffer.frameLength) * ratio)
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: frameCount) else {
            return nil
        }
        output.frameLength = frameCount

        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }

        if let error {
            log.error("Audio conversion failed: \(error.localizedDescription)")
            return nil
        }
        return output
    }

    /// Convert Float32 samples to Int16 little-endian Data for binary WebSocket frames.
    private func float32ToPCM16Data(_ buffer: AVAudioPCMBuffer) -> Data? {
        guard let samples = buffer.floatChannelData?[0] else { return nil }
        let count = Int(buffer.frameLength)
        var data = Data(count: count * 2)
        data.withUnsafeMutableBytes { raw in
            let int16 = raw.bindMemory(to: Int16.self)
            for i in 0..<count {
                let clamped = max(-32768, min(32767, Int32(samples[i] * 32767.0)))
                int16[i] = Int16(clamped).littleEndian
            }
        }
        return data
    }
}

enum AudioCaptureError: LocalizedError {
    case invalidInputFormat
    case converterCreationFailed

    var errorDescription: String? {
        switch self {
        case .invalidInputFormat:
            return "Audio input format has zero channels or sample rate"
        case .converterCreationFailed:
            return "Failed to create AVAudioConverter for sample rate conversion"
        }
    }
}
