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
    const [realtimeSessionId, setRealtimeSessionId] = useState<string | null>(null);
    const [groundingFiles, setGroundingFiles] = useState<GroundingFile[]>([]);
    const [selectedFile, setSelectedFile] = useState<GroundingFile | null>(null);
    const [quoteData, setQuoteData] = useState<QuoteData | null>(null);
    const [userRegistrationData, setUserRegistrationData] = useState<UserRegistrationData | null>(null);
    const [debugMessages, setDebugMessages] = useState<DebugMessage[]>([]);
    const messageIdCounter = useRef(0);
    const audioChunkCounter = useRef(0);
    const audioStoppedBySocketRef = useRef(false);
    const isRecordingRef = useRef(false);
    const quoteFlowControlsRef = useRef({
        stopAudioPlayer: () => {},
        inputAudioBufferClear: () => {},
        speakAssistantMessage: (_text: string) => {},
    });

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

        quoteFlowControlsRef.current.stopAudioPlayer();
        quoteFlowControlsRef.current.inputAudioBufferClear();
        setQuoteData(null);
        quoteFlowControlsRef.current.speakAssistantMessage("I've received your confirmation. I'm processing your quote now.");

        try {
            addDebugMessage("info", "发送报价确认请求", { quote_data: payload });
            const response = await fetch("/api/quotes/confirm", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ quote_data: payload, session_id: realtimeSessionId }),
            });

            if (!response.ok) {
                const error = await response.json();
                addDebugMessage("error", "报价确认失败", { error: error.error || "Failed to send quote" });
                setQuoteData(payload);
                throw new Error(error.error || "Failed to send quote");
            }

            const result = await response.json();
            console.log("Quote sent successfully:", result);
            addDebugMessage("info", "报价确认成功", result);
        } catch (error) {
            console.error("Error confirming quote:", error);
            addDebugMessage("error", "报价确认异常", { error: String(error) });
            throw error;
        }
    }, [quoteData, realtimeSessionId, addDebugMessage]);

    const handleQuoteCancel = useCallback(() => {
        setQuoteData(null);
    }, []);

    const handleUserRegistrationConfirm = useCallback(async (updatedData?: UserRegistrationData) => {
        const payload = updatedData ?? userRegistrationData;
        if (!payload) return;

        quoteFlowControlsRef.current.stopAudioPlayer();
        quoteFlowControlsRef.current.inputAudioBufferClear();
        setUserRegistrationData(null);
        quoteFlowControlsRef.current.speakAssistantMessage("I've received your confirmation. I'm saving your information now.");

        try {
            addDebugMessage("info", "发送用户注册请求", payload);
            const response = await fetch("/api/salesforce/register-user", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ ...payload, session_id: realtimeSessionId }),
            });

            if (!response.ok) {
                const error = await response.json();
                addDebugMessage("error", "用户注册失败", { error: error.error || "Failed to register user" });
                throw new Error(error.error || "Failed to register user");
            }

            const result = await response.json();
            console.log("User registered successfully:", result);
            addDebugMessage("info", "用户注册成功", result);
        } catch (error) {
            console.error("Error registering user:", error);
            addDebugMessage("error", "用户注册异常", { error: String(error) });
            setUserRegistrationData(payload);
            throw error;
        }
    }, [userRegistrationData, realtimeSessionId, addDebugMessage]);

    const handleUserRegistrationCancel = useCallback(() => {
        setUserRegistrationData(null);
    }, []);

    const { startSession, addUserAudio, inputAudioBufferClear, speakAssistantMessage } = useRealTime({
        onWebSocketOpen: () => {
            console.log("WebSocket connection opened");
            addDebugMessage("websocket", "WebSocket连接已打开", { status: "connected" });
        },
        onWebSocketClose: event => {
            console.log("WebSocket connection closed");
            addDebugMessage("websocket", "WebSocket连接已关闭", {
                status: "disconnected",
                code: event.code,
                reason: event.reason || "",
                wasClean: event.wasClean,
            });
            if (isRecording && !audioStoppedBySocketRef.current) {
                audioStoppedBySocketRef.current = true;
                stopAudioRecording()
                    .catch(error => {
                        addDebugMessage("error", "WebSocket关闭后停止录音失败", { error: String(error) });
                    })
                    .finally(() => {
                        stopAudioPlayer();
                        isRecordingRef.current = false;
                        setIsRecording(false);
                        addDebugMessage("info", "WebSocket已断开，自动停止录音", {
                            code: event.code,
                            reason: event.reason || "",
                        });
                    });
            }
        },
        onWebSocketError: event => {
            console.error("WebSocket error:", event);
            addDebugMessage("error", "WebSocket错误", { error: String(event) });
        },
        onWebSocketMessage: (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === "session.created" && message.session?.id) {
                    setRealtimeSessionId(message.session.id);
                }
                if (message.type === "response.done") {
                    const transcript = message.response?.output?.[0]?.content?.[0]?.transcript;
                    if (transcript) addDebugMessage("info", "AI回复", { transcript });
                } else if (message.type === "extension.middle_tier_error") {
                    addDebugMessage("websocket", `WebSocket消息: ${message.type}`, message);
                } else if (message.type === "error") {
                    addDebugMessage("error", "收到错误消息", message);
                }
                if (message.type === "extension.intent_classification") {
                    const classification = message.intent;
                    const transcript = message.transcript || "";
                    addDebugMessage("info", "意图识别", {
                        用户说: transcript,
                        intent: classification?.intent,
                        action: classification?.action,
                        confidence: classification?.confidence,
                    });
                    if (classification?.intent === "quote" && quoteData) {
                        if (classification.action === "confirm") {
                            handleQuoteConfirm().catch(err => console.error("Error in voice quote confirmation:", err));
                        } else if (classification.action === "cancel") {
                            handleQuoteCancel();
                        }
                    } else if (classification?.intent === "registration" && userRegistrationData) {
                        if (classification.action === "confirm") {
                            handleUserRegistrationConfirm().catch(err => console.error("Error in voice user registration confirmation:", err));
                        } else if (classification.action === "cancel") {
                            handleUserRegistrationCancel();
                        }
                    }
                }
            } catch (e) {
                // Ignore parse errors for non-JSON messages
            }
        },
        onReceivedError: message => {
            console.error("realtime error", message);
        },
        onReceivedResponseAudioDelta: message => {
            if (isRecordingRef.current) {
                playAudio(message.delta);
            }
        },
        onReceivedInputAudioBufferSpeechStarted: () => {
            stopAudioPlayer();
        },
        onReceivedInputAudioTranscriptionCompleted: message => {
            addDebugMessage("transcription", "用户说", { transcript: message.transcript });
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
            if (message.tool_name === "extract_quote_info" || message.tool_name === "update_quote_info") {
                if (result.extracted) {
                    const extracted = result.extracted;
                    const quoteItems =
                        extracted.quote_items && Array.isArray(extracted.quote_items) && extracted.quote_items.length > 0
                            ? extracted.quote_items.map(item => ({
                                  product_package: item.product_package || "",
                                  quantity: item.quantity ?? null,
                              }))
                            : extracted.product_package
                              ? [
                                    {
                                        product_package: extracted.product_package || "",
                                        quantity: extracted.quantity ?? null,
                                    },
                                ]
                              : [];

                    const quoteInfo = {
                        customer_name: extracted.customer_name || "",
                        contact_info: extracted.contact_info || "",
                        quote_items: quoteItems,
                        product_package: quoteItems[0]?.product_package || "",
                        quantity: quoteItems[0]?.quantity ?? null,
                        expected_start_date: extracted.expected_start_date ?? null,
                        notes: extracted.notes ?? null,
                    };
                    
                    if (result.is_complete) {
                        console.log("Setting quote data (complete):", quoteInfo);
                        setQuoteData(quoteInfo);
                        addDebugMessage("extracted_info", message.tool_name === "update_quote_info" ? "更新报价信息 (完整)" : "提取报价信息 (完整)", {
                            ...quoteInfo,
                            quote_items: extracted.quote_items,
                            raw_extracted: extracted,
                        });
                    } else {
                        console.log("Quote info incomplete; missing fields:", result.missing_fields);
                        // Still update collected info for display, but don't show confirmation dialog
                        addDebugMessage("extracted_info", message.tool_name === "update_quote_info" ? "更新报价信息 (不完整)" : "提取报价信息 (不完整)", {
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

            if (message.tool_name === "send_quote_email") {
                addDebugMessage("info", "报价邮件发送结果", result);
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
    const { start: startAudioRecording, stop: stopAudioRecording } = useAudioRecorder({
        onAudioRecorded: base64Audio => {
            const sent = addUserAudio(base64Audio);
            if (!sent) return;
            audioChunkCounter.current += 1;
        },
        onError: error => {
            addDebugMessage("error", "录音启动失败", { error: String(error) });
        },
    });

    quoteFlowControlsRef.current.stopAudioPlayer = stopAudioPlayer;
    quoteFlowControlsRef.current.inputAudioBufferClear = inputAudioBufferClear;
    quoteFlowControlsRef.current.speakAssistantMessage = speakAssistantMessage;

    const onToggleListening = async () => {
        if (!isRecording) {
            try {
                audioChunkCounter.current = 0;
                audioStoppedBySocketRef.current = false;
                startSession();
                await startAudioRecording();
                resetAudioPlayer();
                isRecordingRef.current = true;
                setIsRecording(true);
                addDebugMessage("info", "开始录音会话", {});
            } catch (error) {
                isRecordingRef.current = false;
                setIsRecording(false);
                addDebugMessage("error", "开始录音会话失败", { error: String(error) });
            }
        } else {
            await stopAudioRecording();
            stopAudioPlayer();
            inputAudioBufferClear();
            isRecordingRef.current = false;
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
                    onDataChange={(updatedData) => {
                        setUserRegistrationData(updatedData);
                        addDebugMessage("extracted_info", "用户修改注册信息", updatedData);
                    }}
                />
            )}

            <DebugPanel messages={debugMessages} onClear={clearDebugMessages} />
        </div>
    );
}

export default App;
