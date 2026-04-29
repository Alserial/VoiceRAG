import useWebSocket from "react-use-websocket";

import {
    InputAudioBufferAppendCommand,
    InputAudioBufferClearCommand,
    Message,
    ResponseAudioDelta,
    ResponseAudioTranscriptDelta,
    ResponseDone,
    SessionUpdateCommand,
    ExtensionMiddleTierToolResponse,
    ResponseInputAudioTranscriptionCompleted
} from "@/types";

type Parameters = {
    useDirectAoaiApi?: boolean; // If true, the middle tier will be skipped and the AOAI ws API will be called directly
    aoaiEndpointOverride?: string;
    aoaiApiKeyOverride?: string;
    aoaiModelOverride?: string;

    onWebSocketOpen?: () => void;
    onWebSocketClose?: (event: CloseEvent) => void;
    onWebSocketError?: (event: Event) => void;
    onWebSocketMessage?: (event: MessageEvent<any>) => void;

    onReceivedResponseAudioDelta?: (message: ResponseAudioDelta) => void;
    onReceivedInputAudioBufferSpeechStarted?: (message: Message) => void;
    onReceivedResponseDone?: (message: ResponseDone) => void;
    onReceivedExtensionMiddleTierToolResponse?: (message: ExtensionMiddleTierToolResponse) => void;
    onReceivedResponseAudioTranscriptDelta?: (message: ResponseAudioTranscriptDelta) => void;
    onReceivedInputAudioTranscriptionCompleted?: (message: ResponseInputAudioTranscriptionCompleted) => void;
    onReceivedError?: (message: Message) => void;
};

export default function useRealTime({
    useDirectAoaiApi,
    aoaiEndpointOverride,
    aoaiApiKeyOverride,
    aoaiModelOverride,
    onWebSocketOpen,
    onWebSocketClose,
    onWebSocketError,
    onWebSocketMessage,
    onReceivedResponseDone,
    onReceivedResponseAudioDelta,
    onReceivedResponseAudioTranscriptDelta,
    onReceivedInputAudioBufferSpeechStarted,
    onReceivedExtensionMiddleTierToolResponse,
    onReceivedInputAudioTranscriptionCompleted,
    onReceivedError
}: Parameters) {
    const wsEndpoint = useDirectAoaiApi
        ? `${aoaiEndpointOverride}/openai/v1/realtime?api-key=${aoaiApiKeyOverride}&model=${aoaiModelOverride}`
        : `/realtime`;

    const { sendJsonMessage, readyState } = useWebSocket(wsEndpoint, {
        onOpen: () => onWebSocketOpen?.(),
        onClose: event => onWebSocketClose?.(event),
        onError: event => onWebSocketError?.(event),
        onMessage: event => onMessageReceived(event),
        shouldReconnect: () => true
    });

    const startSession = () => {
        const command: SessionUpdateCommand = {
            type: "session.update",
            session: {}
        };

        sendJsonMessage(command);
    };

    const addUserAudio = (base64Audio: string) => {
        if (readyState !== WebSocket.OPEN) {
            return false;
        }

        const command: InputAudioBufferAppendCommand = {
            type: "input_audio_buffer.append",
            audio: base64Audio
        };

        sendJsonMessage(command, false);
        return true;
    };

    const inputAudioBufferClear = () => {
        const command: InputAudioBufferClearCommand = {
            type: "input_audio_buffer.clear"
        };

        sendJsonMessage(command);
    };

    const speakAssistantMessage = (text: string) => {
        if (!text.trim()) return;

        sendJsonMessage({
            type: "response.create",
            response: {
                modalities: ["audio", "text"],
                instructions: `Say exactly this sentence in English: ${text}`
            }
        });
    };

    const onMessageReceived = (event: MessageEvent<any>) => {
        onWebSocketMessage?.(event);

        let message: Message;
        try {
            message = JSON.parse(event.data);
        } catch (e) {
            console.error("Failed to parse JSON message:", e);
            throw e;
        }

        switch (message.type) {
            case "response.done":
                onReceivedResponseDone?.(message as ResponseDone);
                break;
            case "response.output_audio.delta":
                onReceivedResponseAudioDelta?.(message as ResponseAudioDelta);
                break;
            case "response.output_audio_transcript.delta":
                onReceivedResponseAudioTranscriptDelta?.(message as ResponseAudioTranscriptDelta);
                break;
            case "input_audio_buffer.speech_started":
                onReceivedInputAudioBufferSpeechStarted?.(message);
                break;
            case "conversation.item.input_audio_transcription.completed":
                onReceivedInputAudioTranscriptionCompleted?.(message as ResponseInputAudioTranscriptionCompleted);
                break;
            case "extension.middle_tier_tool_response":
                console.log("Received extension.middle_tier_tool_response:", message);
                onReceivedExtensionMiddleTierToolResponse?.(message as ExtensionMiddleTierToolResponse);
                break;
            case "error":
                onReceivedError?.(message);
                break;
            case "conversation.item.created":
                // Check if this is a function_call that we need to track
                if (message.item && message.item.type === "function_call") {
                    console.log("Function call detected:", message.item.name, message);
                }
                // Don't break, let it fall through to default for now
                break;
            default:
                // Log unhandled message types for debugging (but filter out common ones)
                const commonTypes = ["session.created", "session.updated", "input_audio_buffer.speech_stopped", 
                                     "input_audio_buffer.committed", "response.output_item.added"];
                if (message.type && !commonTypes.includes(message.type) && 
                    !message.type.startsWith("response.") && 
                    !message.type.startsWith("input_audio_buffer.")) {
                    console.log("Unhandled message type:", message.type, message);
                }
                break;
        }
    };

    return { startSession, addUserAudio, inputAudioBufferClear, speakAssistantMessage };
}
