import { useState, useCallback } from "react";
import { Mic, MicOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { GroundingFiles } from "@/components/ui/grounding-files";
import GroundingFileView from "@/components/ui/grounding-file-view";
import StatusMessage from "@/components/ui/status-message";

import useRealTime from "@/hooks/useRealtime";
import useAudioRecorder from "@/hooks/useAudioRecorder";
import useAudioPlayer from "@/hooks/useAudioPlayer";

import { GroundingFile, ToolResult } from "./types";

import logo from "./assets/logo.svg";
import QuoteRequestForm from "@/components/quote/QuoteRequestForm";
import QuoteConfirmation, { QuoteData } from "@/components/quote/QuoteConfirmation";

function App() {
    const [isRecording, setIsRecording] = useState(false);
    const [groundingFiles, setGroundingFiles] = useState<GroundingFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<GroundingFile | null>(null);
    const [quoteData, setQuoteData] = useState<QuoteData | null>(null);

    const handleQuoteConfirm = useCallback(async () => {
        if (!quoteData) return;

        try {
            const response = await fetch("/api/quotes/confirm", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ quote_data: quoteData }),
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || "Failed to send quote");
            }

            const result = await response.json();
            console.log("Quote sent successfully:", result);
        } catch (error) {
            console.error("Error confirming quote:", error);
            throw error;
        }
    }, [quoteData]);

    const handleQuoteCancel = useCallback(() => {
        setQuoteData(null);
    }, []);

    // Listen for voice confirmation
    const checkVoiceConfirmation = useCallback((transcript: string) => {
        const confirmKeywords = ["confirm", "yes", "send", "ok", "okay", "proceed", "go ahead"];
        const cancelKeywords = ["cancel", "no", "stop", "wait"];
        
        const lowerTranscript = transcript.toLowerCase();
        
        if (confirmKeywords.some(keyword => lowerTranscript.includes(keyword))) {
            if (quoteData) {
                handleQuoteConfirm();
            }
        } else if (cancelKeywords.some(keyword => lowerTranscript.includes(keyword))) {
            handleQuoteCancel();
        }
    }, [quoteData, handleQuoteConfirm, handleQuoteCancel]);

    const { startSession, addUserAudio, inputAudioBufferClear } = useRealTime({
        enableInputAudioTranscription: true,  // Enable input audio transcription to capture user input
        onWebSocketOpen: () => console.log("WebSocket connection opened"),
        onWebSocketClose: () => console.log("WebSocket connection closed"),
        onWebSocketError: event => console.error("WebSocket error:", event),
        onReceivedError: message => console.error("error", message),
        onReceivedResponseAudioDelta: message => {
            isRecording && playAudio(message.delta);
        },
        onReceivedInputAudioBufferSpeechStarted: () => {
            stopAudioPlayer();
        },
        onReceivedInputAudioTranscriptionCompleted: message => {
            // Check for voice confirmation when quote is pending
            if (quoteData && message.transcript) {
                checkVoiceConfirmation(message.transcript);
            }
        },
        onReceivedExtensionMiddleTierToolResponse: message => {
            console.log("Received tool response:", message);
            const result: ToolResult = JSON.parse(message.tool_result);
            console.log("Parsed tool result:", result);

            // Handle quote extraction result
            if (message.tool_name === "extract_quote_info" && result.status === "complete" && result.quote_data) {
                console.log("Setting quote data:", result.quote_data);
                setQuoteData(result.quote_data);
                return;
            }

            // Handle grounding files (search results)
            if (result.sources) {
                const files: GroundingFile[] = result.sources.map(x => {
                    return { id: x.chunk_id, name: x.title, content: x.chunk };
                });
                setGroundingFiles(prev => [...prev, ...files]);
            }
        }
    });

    const { reset: resetAudioPlayer, play: playAudio, stop: stopAudioPlayer } = useAudioPlayer();
    const { start: startAudioRecording, stop: stopAudioRecording } = useAudioRecorder({ onAudioRecorded: addUserAudio });

    const onToggleListening = async () => {
        if (!isRecording) {
            startSession();
            await startAudioRecording();
            resetAudioPlayer();

            setIsRecording(true);
        } else {
            await stopAudioRecording();
            stopAudioPlayer();
            inputAudioBufferClear();

            setIsRecording(false);
        }
    };

    const { t } = useTranslation();

    return (
        <div className="flex min-h-screen flex-col bg-gray-100 text-gray-900">
            <div className="p-4 sm:absolute sm:left-4 sm:top-4">
                <img src={logo} alt="Azure logo" className="h-16 w-16" />
            </div>
            <main className="flex flex-grow flex-col items-center justify-center">
                <h1 className="mb-8 bg-gradient-to-r from-purple-600 to-pink-600 bg-clip-text text-4xl font-bold text-transparent md:text-7xl">
                    {t("app.title")}
                </h1>
                <div className="mb-4 flex flex-col items-center justify-center">
                    <Button
                        onClick={onToggleListening}
                        className={`h-12 w-60 ${isRecording ? "bg-red-600 hover:bg-red-700" : "bg-purple-500 hover:bg-purple-600"}`}
                        aria-label={isRecording ? t("app.stopRecording") : t("app.startRecording")}
                    >
                        {isRecording ? (
                            <>
                                <MicOff className="mr-2 h-4 w-4" />
                                {t("app.stopConversation")}
                            </>
                        ) : (
                            <>
                                <Mic className="mr-2 h-6 w-6" />
                            </>
                        )}
                    </Button>
                    <StatusMessage isRecording={isRecording} />
                </div>
                <GroundingFiles files={groundingFiles} onSelected={setSelectedFile} />
                <div className="mt-8 w-full px-4 pb-12">
                    <QuoteRequestForm />
                </div>
            </main>

            <footer className="py-4 text-center">
                <p>{t("app.footer")}</p>
            </footer>

            {/* Version number - small and unobtrusive */}
            <div className="fixed bottom-2 right-2 text-xs text-gray-600 opacity-80">
                v2.2.0
            </div>

            <GroundingFileView groundingFile={selectedFile} onClosed={() => setSelectedFile(null)} />
            
            {quoteData && (
                <QuoteConfirmation
                    quoteData={quoteData}
                    onConfirm={handleQuoteConfirm}
                    onCancel={handleQuoteCancel}
                />
            )}
        </div>
    );
}

export default App;
