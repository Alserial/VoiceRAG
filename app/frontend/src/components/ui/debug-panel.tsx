import { useState, useRef, useEffect, useMemo } from "react";
import { Bug, ChevronDown, ChevronUp, X, Trash2, User, Mail, Package, Calendar, FileText } from "lucide-react";
import { Button } from "./button";
import { Card, CardContent, CardHeader, CardTitle } from "./card";

export type DebugMessage = {
    id: string;
    timestamp: Date;
    type: "websocket" | "tool_response" | "extracted_info" | "transcription" | "error" | "info";
    title: string;
    data: any;
};

type CollectedInfo = {
    customer_name?: string | null;
    contact_info?: string | null;
    product_package?: string | null;
    quantity?: number | null;
    quote_items?: Array<{ product_package?: string; quantity?: number }> | null;
    expected_start_date?: string | null;
    notes?: string | null;
};

type DebugPanelProps = {
    messages: DebugMessage[];
    onClear: () => void;
};

export default function DebugPanel({ messages, onClear }: DebugPanelProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [autoScroll, setAutoScroll] = useState(true);
    const [viewMode, setViewMode] = useState<"messages" | "collected">("collected");
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Extract collected information from messages
    const collectedInfo = useMemo<CollectedInfo>(() => {
        const info: CollectedInfo = {};
        
        // Find the most recent extracted_info message
        const extractedMessages = messages
            .filter(msg => msg.type === "extracted_info")
            .sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
        
        for (const msg of extractedMessages) {
            const data = msg.data;
            
            // Handle user registration data
            if (data.customer_name !== undefined) {
                info.customer_name = data.customer_name || null;
            }
            if (data.contact_info !== undefined) {
                info.contact_info = data.contact_info || null;
            }
            
            // Handle quote data (may have extracted object)
            if (data.extracted) {
                const extracted = data.extracted;
                if (extracted.customer_name !== undefined) {
                    info.customer_name = extracted.customer_name || null;
                }
                if (extracted.contact_info !== undefined) {
                    info.contact_info = extracted.contact_info || null;
                }
                if (extracted.product_package !== undefined) {
                    info.product_package = extracted.product_package || null;
                }
                if (extracted.quantity !== undefined) {
                    info.quantity = extracted.quantity || null;
                }
                if (extracted.quote_items !== undefined) {
                    info.quote_items = extracted.quote_items || null;
                }
                if (extracted.expected_start_date !== undefined) {
                    info.expected_start_date = extracted.expected_start_date || null;
                }
                if (extracted.notes !== undefined) {
                    info.notes = extracted.notes || null;
                }
            } else {
                // Direct fields
                if (data.product_package !== undefined) {
                    info.product_package = data.product_package || null;
                }
                if (data.quantity !== undefined) {
                    info.quantity = data.quantity || null;
                }
                if (data.quote_items !== undefined) {
                    info.quote_items = data.quote_items || null;
                }
                if (data.expected_start_date !== undefined) {
                    info.expected_start_date = data.expected_start_date || null;
                }
                if (data.notes !== undefined) {
                    info.notes = data.notes || null;
                }
            }
        }
        
        return info;
    }, [messages]);

    useEffect(() => {
        if (autoScroll && isOpen && messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
        }
    }, [messages, autoScroll, isOpen]);

    const formatTimestamp = (date: Date) => {
        const timeStr = date.toLocaleTimeString("en-US", { 
            hour12: false, 
            hour: "2-digit", 
            minute: "2-digit", 
            second: "2-digit"
        });
        const ms = date.getMilliseconds().toString().padStart(3, '0');
        return `${timeStr}.${ms}`;
    };

    const formatData = (data: any): string => {
        if (typeof data === "string") {
            try {
                const parsed = JSON.parse(data);
                return JSON.stringify(parsed, null, 2);
            } catch {
                return data;
            }
        }
        return JSON.stringify(data, null, 2);
    };

    const getTypeColor = (type: DebugMessage["type"]) => {
        switch (type) {
            case "websocket":
                return "bg-blue-100 text-blue-800 border-blue-300";
            case "tool_response":
                return "bg-green-100 text-green-800 border-green-300";
            case "extracted_info":
                return "bg-purple-100 text-purple-800 border-purple-300";
            case "transcription":
                return "bg-yellow-100 text-yellow-800 border-yellow-300";
            case "error":
                return "bg-red-100 text-red-800 border-red-300";
            case "info":
                return "bg-gray-100 text-gray-800 border-gray-300";
            default:
                return "bg-gray-100 text-gray-800 border-gray-300";
        }
    };

    return (
        <div className="fixed bottom-4 right-4 z-40">
            <Button
                onClick={() => setIsOpen(!isOpen)}
                className="mb-2 bg-gray-700 hover:bg-gray-600 text-white"
                size="sm"
            >
                <Bug className="h-4 w-4 mr-2" />
                Debug Panel {messages.length > 0 && `(${messages.length})`}
                {isOpen ? (
                    <ChevronDown className="h-4 w-4 ml-2" />
                ) : (
                    <ChevronUp className="h-4 w-4 ml-2" />
                )}
            </Button>

            {isOpen && (
                <Card className="w-[600px] max-h-[70vh] flex flex-col shadow-2xl">
                    <CardHeader className="pb-3">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-lg">Debug Panel</CardTitle>
                            <div className="flex gap-2">
                                <div className="flex gap-1 border rounded-md">
                                    <Button
                                        variant={viewMode === "collected" ? "default" : "ghost"}
                                        size="sm"
                                        onClick={() => setViewMode("collected")}
                                        className="text-xs h-7"
                                    >
                                        Collected Info
                                    </Button>
                                    <Button
                                        variant={viewMode === "messages" ? "default" : "ghost"}
                                        size="sm"
                                        onClick={() => setViewMode("messages")}
                                        className="text-xs h-7"
                                    >
                                        Debug Log
                                    </Button>
                                </div>
                                {viewMode === "messages" && (
                                    <>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setAutoScroll(!autoScroll)}
                                            className="text-xs"
                                        >
                                            {autoScroll ? "Auto Scroll: On" : "Auto Scroll: Off"}
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={onClear}
                                            className="text-xs text-red-600 hover:text-red-700"
                                        >
                                            <Trash2 className="h-4 w-4 mr-1" />
                                            Clear
                                        </Button>
                                    </>
                                )}
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setIsOpen(false)}
                                >
                                    <X className="h-4 w-4" />
                                </Button>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="flex-1 overflow-hidden p-0 flex flex-col">
                        {viewMode === "collected" ? (
                            <div className="flex-1 overflow-y-auto p-4">
                                <div className="space-y-4">
                                    <div className="border rounded-lg p-4 bg-gray-50">
                                        <div className="flex items-center gap-2 mb-3">
                                            <User className="h-4 w-4 text-gray-600" />
                                            <label className="text-sm font-medium text-gray-700">Name</label>
                                        </div>
                                        <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[2rem]">
                                            {collectedInfo.customer_name || <span className="text-gray-400">(Not collected)</span>}
                                        </div>
                                    </div>

                                    <div className="border rounded-lg p-4 bg-gray-50">
                                        <div className="flex items-center gap-2 mb-3">
                                            <Mail className="h-4 w-4 text-gray-600" />
                                            <label className="text-sm font-medium text-gray-700">Email</label>
                                        </div>
                                        <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[2rem]">
                                            {collectedInfo.contact_info || <span className="text-gray-400">(Not collected)</span>}
                                        </div>
                                    </div>

                                    <div className="border rounded-lg p-4 bg-gray-50">
                                        <div className="flex items-center gap-2 mb-3">
                                            <Package className="h-4 w-4 text-gray-600" />
                                            <label className="text-sm font-medium text-gray-700">Product</label>
                                        </div>
                                        <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[2rem]">
                                            {(() => {
                                                if (collectedInfo.quote_items && collectedInfo.quote_items.length > 0) {
                                                    return collectedInfo.quote_items.map((item, idx) => (
                                                        <div key={idx} className="mb-1">
                                                            {item.product_package || <span className="text-gray-400">(Not specified)</span>}
                                                            {item.quantity !== undefined && item.quantity !== null && (
                                                                <span className="text-gray-600 ml-2">x {item.quantity}</span>
                                                            )}
                                                        </div>
                                                    ));
                                                }
                                                return collectedInfo.product_package || <span className="text-gray-400">(Not collected)</span>;
                                            })()}
                                        </div>
                                    </div>

                                    {collectedInfo.quantity !== undefined && collectedInfo.quantity !== null && !collectedInfo.quote_items && (
                                        <div className="border rounded-lg p-4 bg-gray-50">
                                            <div className="flex items-center gap-2 mb-3">
                                                <Package className="h-4 w-4 text-gray-600" />
                                                <label className="text-sm font-medium text-gray-700">Quantity</label>
                                            </div>
                                            <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[2rem]">
                                                {collectedInfo.quantity}
                                            </div>
                                        </div>
                                    )}

                                    <div className="border rounded-lg p-4 bg-gray-50">
                                        <div className="flex items-center gap-2 mb-3">
                                            <Calendar className="h-4 w-4 text-gray-600" />
                                            <label className="text-sm font-medium text-gray-700">Expected Start Date</label>
                                        </div>
                                        <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[2rem]">
                                            {collectedInfo.expected_start_date || <span className="text-gray-400">(Not collected)</span>}
                                        </div>
                                    </div>

                                    <div className="border rounded-lg p-4 bg-gray-50">
                                        <div className="flex items-center gap-2 mb-3">
                                            <FileText className="h-4 w-4 text-gray-600" />
                                            <label className="text-sm font-medium text-gray-700">Notes</label>
                                        </div>
                                        <div className="text-sm text-gray-900 bg-white border border-gray-200 rounded px-3 py-2 min-h-[4rem]">
                                            {collectedInfo.notes || <span className="text-gray-400">(Not collected)</span>}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <div className="flex-1 overflow-y-auto p-4 space-y-3">
                                {messages.length === 0 ? (
                                    <div className="text-center text-gray-500 py-8">
                                        No debug messages
                                    </div>
                                ) : (
                                    messages.map((msg) => (
                                        <div
                                            key={msg.id}
                                            className={`border rounded-lg p-3 text-sm ${getTypeColor(msg.type)}`}
                                        >
                                            <div className="flex items-start justify-between mb-2">
                                                <div className="flex-1">
                                                    <div className="font-semibold text-xs mb-1">
                                                        [{msg.type.toUpperCase()}] {msg.title}
                                                    </div>
                                                    <div className="text-xs opacity-70">
                                                        {formatTimestamp(msg.timestamp)}
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="mt-2">
                                                <pre className="text-xs bg-white bg-opacity-50 rounded p-2 overflow-x-auto max-h-48 overflow-y-auto">
                                                    <code>{formatData(msg.data)}</code>
                                                </pre>
                                            </div>
                                        </div>
                                    ))
                                )}
                                <div ref={messagesEndRef} />
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
