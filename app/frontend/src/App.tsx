import { useState, useCallback, useRef } from "react";
import { Mic, MicOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { GroundingFiles } from "@/components/ui/grounding-files";
import GroundingFileView from "@/components/ui/grounding-file-view";
import StatusMessage from "@/components/ui/status-message";
import DebugPanel, { DebugMessage } from "@/components/ui/debug-panel";

import useRealTime from "@/hooks/useRealtime";
import useAudioRecorder from "@/hooks/useAudioRecorder";
import useAudioPlayer from "@/hooks/useAudioPlayer";

import { GroundingFile, ToolResult } from "./types";

import logo from "./assets/logo.svg";
import QuoteConfirmation, { QuoteData } from "@/components/quote/QuoteConfirmation";
import UserRegistrationConfirmation, { UserRegistrationData } from "@/components/user/UserRegistrationConfirmation";

function App() {
    const [isRecording, setIsRecording] = useState(false);
    const [groundingFiles, setGroundingFiles] = useState<GroundingFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<GroundingFile | null>(null);
    const [quoteData, setQuoteData] = useState<QuoteData | null>(null);
    const [userRegistrationData, setUserRegistrationData] = useState<UserRegistrationData | null>(null);
    const [debugMessages, setDebugMessages] = useState<DebugMessage[]>([]);
    const messageIdCounter = useRef(0);

    // Helper function to add debug messages
    const addDebugMessage = useCallback((type: DebugMessage["type"], title: string, data: any) => {
        const id = `msg-${++messageIdCounter.current}`;
        const message: DebugMessage = {
            id,
            timestamp: new Date(),
            type,
            title,
            data,
        };
        setDebugMessages(prev => [...prev, message]);
    }, []);

    const clearDebugMessages = useCallback(() => {
        setDebugMessages([]);
    }, []);

    const handleQuoteConfirm = useCallback(async (updatedQuote?: QuoteData) => {
        const payload = updatedQuote ?? quoteData;
        if (!payload) return;

        try {
            addDebugMessage("info", "发送报价确认请求", { quote_data: payload });
            const response = await fetch("/api/quotes/confirm", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ quote_data: payload }),
            });

            if (!response.ok) {
                const error = await response.json();
                addDebugMessage("error", "报价确认失败", { error: error.error || "Failed to send quote" });
                throw new Error(error.error || "Failed to send quote");
            }

            const result = await response.json();
            console.log("Quote sent successfully:", result);
            addDebugMessage("info", "报价确认成功", result);
            // Close the dialog after successful confirmation
            setQuoteData(null);
        } catch (error) {
            console.error("Error confirming quote:", error);
            addDebugMessage("error", "报价确认异常", { error: String(error) });
            throw error;
        }
    }, [quoteData, addDebugMessage]);

    const handleQuoteCancel = useCallback(() => {
        setQuoteData(null);
    }, []);

    const handleUserRegistrationConfirm = useCallback(async (updatedData?: UserRegistrationData) => {
        const payload = updatedData ?? userRegistrationData;
        if (!payload) return;

        try {
            addDebugMessage("info", "发送用户注册请求", payload);
            const response = await fetch("/api/salesforce/register-user", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const error = await response.json();
                addDebugMessage("error", "用户注册失败", { error: error.error || "Failed to register user" });
                throw new Error(error.error || "Failed to register user");
            }

            const result = await response.json();
            console.log("User registered successfully:", result);
            addDebugMessage("info", "用户注册成功", result);
            // Close the dialog after successful registration
            setUserRegistrationData(null);
        } catch (error) {
            console.error("Error registering user:", error);
            addDebugMessage("error", "用户注册异常", { error: String(error) });
            throw error;
        }
    }, [userRegistrationData, addDebugMessage]);

    const handleUserRegistrationCancel = useCallback(() => {
        setUserRegistrationData(null);
    }, []);

    // Listen for voice state via backend LLM classifier (except very explicit confirmations)
    const checkVoiceConfirmation = useCallback(async (transcript: string) => {
        const lowerTranscript = transcript.toLowerCase().trim();
        const explicitConfirmSet = new Set(["yes", "confirm"]);
        const pendingAction = quoteData ? "quote" : userRegistrationData ? "user_registration" : "none";

        if (pendingAction === "none") {
            return;
        }

        const handleConfirmForPendingAction = () => {
            if (quoteData) {
                addDebugMessage("info", "检测到语音确认 - 报价", { transcript, source: "explicit_or_llm" });
                handleQuoteConfirm().catch(err => {
                    console.error("Error in voice quote confirmation:", err);
                });
                return;
            }
            if (userRegistrationData) {
                addDebugMessage("info", "检测到语音确认 - 用户注册", { transcript, source: "explicit_or_llm" });
                handleUserRegistrationConfirm().catch(err => {
                    console.error("Error in voice user registration confirmation:", err);
                });
            }
        };

        const handleCancelForPendingAction = () => {
            if (quoteData) {
                addDebugMessage("info", "检测到语音取消 - 报价", { transcript, source: "llm" });
                handleQuoteCancel();
                return;
            }
            if (userRegistrationData) {
                addDebugMessage("info", "检测到语音取消 - 用户注册", { transcript, source: "llm" });
                handleUserRegistrationCancel();
            }
        };

        // Keep explicit confirms as fast path
        if (explicitConfirmSet.has(lowerTranscript)) {
            handleConfirmForPendingAction();
            return;
        }

        try {
            const response = await fetch("/api/utterance-state", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ transcript, pending_action: pendingAction }),
            });

            if (!response.ok) {
                addDebugMessage("error", "语音状态识别失败", { transcript, status: response.status });
                return;
            }

            const result = await response.json();
            addDebugMessage("info", "语音行为状态识别结果", { transcript, result });

            if (result.state === "confirm") {
                handleConfirmForPendingAction();
            } else if (result.state === "cancel") {
                handleCancelForPendingAction();
            }
        } catch (err) {
            console.error("Error in utterance state classification:", err);
            addDebugMessage("error", "语音状态识别异常", { transcript, error: String(err) });
        }
    }, [quoteData, userRegistrationData, handleQuoteConfirm, handleQuoteCancel, handleUserRegistrationConfirm, handleUserRegistrationCancel, addDebugMessage]);

    const { startSession, addUserAudio, inputAudioBufferClear } = useRealTime({
        enableInputAudioTranscription: true,  // Enable input audio transcription to capture user input
        onWebSocketOpen: () => {
            console.log("WebSocket connection opened");
            addDebugMessage("websocket", "WebSocket连接已打开", { status: "connected" });
        },
        onWebSocketClose: () => {
            console.log("WebSocket connection closed");
            addDebugMessage("websocket", "WebSocket连接已关闭", { status: "disconnected" });
        },
        onWebSocketError: event => {
            console.error("WebSocket error:", event);
            addDebugMessage("error", "WebSocket错误", { error: String(event) });
        },
        onWebSocketMessage: (event) => {
            try {
                const message = JSON.parse(event.data);
                // Only log important message types to avoid too much noise
                const importantTypes = [
                    "response.done",
                    "response.audio_transcript.delta",
                    "conversation.item.created",
                    "conversation.item.input_audio_transcription.completed",
                    "extension.middle_tier_tool_response",
                    "error"
                ];
                if (importantTypes.includes(message.type) || message.type?.includes("tool") || message.type?.includes("function")) {
                    addDebugMessage("websocket", `WebSocket消息: ${message.type}`, message);
                }
            } catch (e) {
                // Ignore parse errors for non-JSON messages
            }
        },
        onReceivedError: message => {
            console.error("error", message);
            addDebugMessage("error", "收到错误消息", message);
        },
        onReceivedResponseAudioDelta: message => {
            isRecording && playAudio(message.delta);
        },
        onReceivedInputAudioBufferSpeechStarted: () => {
            stopAudioPlayer();
            addDebugMessage("info", "检测到用户开始说话", {});
        },
        onReceivedInputAudioTranscriptionCompleted: message => {
            addDebugMessage("transcription", "用户输入转录完成", {
                transcript: message.transcript,
                item_id: message.item_id,
            });
            // Check for voice confirmation when quote or user registration is pending
            if (message.transcript && (quoteData || userRegistrationData)) {
                checkVoiceConfirmation(message.transcript).catch(err => {
                    console.error("Error while checking voice confirmation:", err);
                });
            }
        },
        onReceivedExtensionMiddleTierToolResponse: message => {
            console.log("Received tool response:", message);
            const result: ToolResult = JSON.parse(message.tool_result);
            console.log("Parsed tool result:", result);

            // Add debug message for tool response
            addDebugMessage("tool_response", `工具响应: ${message.tool_name}`, {
                tool_name: message.tool_name,
                previous_item_id: message.previous_item_id,
                result: result,
            });

            // Handle user registration
            if (message.tool_name === "extract_user_info") {
                if (result.is_complete && result.extracted) {
                    console.log("Setting user registration data (complete):", result.extracted);
                    const extracted = result.extracted;
                    const userData = {
                        customer_name: extracted.customer_name || "",
                        contact_info: extracted.contact_info || "",
                    };
                    setUserRegistrationData(userData);
                    addDebugMessage("extracted_info", "提取用户注册信息 (完整)", userData);
                } else {
                    console.log("User info incomplete");
                    addDebugMessage("extracted_info", "提取用户注册信息 (不完整)", {
                        extracted: result.extracted,
                        missing_fields: result.missing_fields,
                    });
                }
                return;
            }

            // Handle quote extraction state
            if (message.tool_name === "extract_quote_info") {
                if (result.extracted) {
                    const extracted = result.extracted;
                    
                    // Handle quote_items array (multiple products support)
                    let productPackage = "";
                    let quantity: number | null = null;
                    
                    if (extracted.quote_items && Array.isArray(extracted.quote_items) && extracted.quote_items.length > 0) {
                        // Use the first product for display (supporting multiple products)
                        const firstItem = extracted.quote_items[0];
                        productPackage = firstItem.product_package || "";
                        quantity = firstItem.quantity ?? null;
                    } else if (extracted.product_package) {
                        // Fallback to legacy format
                        productPackage = extracted.product_package || "";
                        quantity = extracted.quantity ?? null;
                    }
                    
                    const quoteInfo = {
                        customer_name: extracted.customer_name || "",
                        contact_info: extracted.contact_info || "",
                        product_package: productPackage,
                        quantity: quantity,
                        expected_start_date: extracted.expected_start_date ?? null,
                        notes: extracted.notes ?? null,
                    };
                    
                    if (result.is_complete) {
                        console.log("Setting quote data (complete):", quoteInfo);
                        setQuoteData(quoteInfo);
                        addDebugMessage("extracted_info", "提取报价信息 (完整)", {
                            ...quoteInfo,
                            quote_items: extracted.quote_items,
                            raw_extracted: extracted,
                        });
                    } else {
                        console.log("Quote info incomplete; missing fields:", result.missing_fields);
                        // Still update collected info for display, but don't show confirmation dialog
                        addDebugMessage("extracted_info", "提取报价信息 (不完整)", {
                            ...quoteInfo,
                            quote_items: extracted.quote_items,
                            missing_fields: result.missing_fields,
                            products_available: result.products_available,
                            raw_extracted: extracted,
                        });
                    }
                } else {
                    console.log("Quote extraction result has no extracted data");
                    addDebugMessage("extracted_info", "提取报价信息 (无数据)", {
                        missing_fields: result.missing_fields || [],
                        products_available: result.products_available || [],
                    });
                }
                return;
            }

            // Handle grounding files (search results)
            if (result.sources) {
                const files: GroundingFile[] = result.sources.map(x => {
                    return { id: x.chunk_id, name: x.title, content: x.chunk };
                });
                setGroundingFiles(prev => [...prev, ...files]);
                addDebugMessage("tool_response", "搜索到文档", {
                    count: files.length,
                    sources: files.map(f => ({ id: f.id, name: f.name })),
                });
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
            addDebugMessage("info", "开始录音会话", {});
        } else {
            await stopAudioRecording();
            stopAudioPlayer();
            inputAudioBufferClear();
            setIsRecording(false);
            addDebugMessage("info", "停止录音会话", {});
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
            </main>

            <footer className="py-4 text-center">
                <p>{t("app.footer")}</p>
            </footer>

            {/* Version number - small and unobtrusive */}
            <div className="fixed bottom-2 left-2 text-xs text-gray-600 opacity-80">
                v2.2.0
            </div>

            <GroundingFileView groundingFile={selectedFile} onClosed={() => setSelectedFile(null)} />
            
            {quoteData && (
                <QuoteConfirmation
                    initialQuoteData={quoteData}
                    onConfirm={handleQuoteConfirm}
                    onCancel={handleQuoteCancel}
                    onDataChange={(updatedData) => {
                        // Sync updated data back to parent state
                        setQuoteData(updatedData);
                        addDebugMessage("extracted_info", "用户修改报价信息", updatedData);
                    }}
                />
            )}

            {userRegistrationData && (
                <UserRegistrationConfirmation
                    initialUserData={userRegistrationData}
                    onConfirm={handleUserRegistrationConfirm}
                    onCancel={handleUserRegistrationCancel}
                />
            )}

            <DebugPanel messages={debugMessages} onClear={clearDebugMessages} />
        </div>
    );
}

export default App;
