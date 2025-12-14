import { useState } from "react";
import { Check, X, Mail, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export type QuoteData = {
    customer_name: string;
    contact_info: string;
    product_package: string;
    quantity: number | null;
    expected_start_date?: string | null;
    notes?: string | null;
};

type QuoteConfirmationProps = {
    initialQuoteData: QuoteData;
    onConfirm: (data?: QuoteData) => Promise<void>;
    onCancel: () => void;
};

export default function QuoteConfirmation({ initialQuoteData, onConfirm, onCancel }: QuoteConfirmationProps) {
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isSuccess, setIsSuccess] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [quoteData, setQuoteData] = useState<QuoteData>(initialQuoteData);

    const handleConfirm = async () => {
        setIsSubmitting(true);
        setError(null);
        try {
            await onConfirm(quoteData);
            setIsSuccess(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to send quote");
        } finally {
            setIsSubmitting(false);
        }
    };

    if (isSuccess) {
        return (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
                <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
                    <div className="flex flex-col items-center text-center">
                        <div className="bg-green-100 rounded-full p-3 mb-4">
                            <Check className="h-8 w-8 text-green-600" />
                        </div>
                        <h2 className="text-2xl font-bold text-gray-900 mb-2">Quote Sent!</h2>
                        <p className="text-gray-600 mb-4">
                            Your quote has been sent to <strong>{quoteData.contact_info}</strong>
                        </p>
                        <Button onClick={onCancel} className="w-full">
                            Close
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
            <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-2xl font-bold text-gray-900">Review Quote Information</h2>
                    <button
                        onClick={onCancel}
                        className="text-gray-400 hover:text-gray-600 transition-colors"
                        aria-label="Close"
                    >
                        <X className="h-6 w-6" />
                    </button>
                </div>

                <div className="space-y-4 mb-6">
                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Customer Name</label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            value={quoteData.customer_name}
                            onChange={e => setQuoteData(prev => ({ ...prev, customer_name: e.target.value }))}
                        />
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Contact Information (Email)</label>
                        <div className="flex items-center gap-2">
                            <Mail className="h-4 w-4 text-gray-500" />
                            <input
                                className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                                value={quoteData.contact_info}
                                onChange={e => setQuoteData(prev => ({ ...prev, contact_info: e.target.value }))}
                            />
                        </div>
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Product/Package</label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            value={quoteData.product_package}
                            onChange={e => setQuoteData(prev => ({ ...prev, product_package: e.target.value }))}
                        />
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Quantity</label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            type="number"
                            min={1}
                            value={quoteData.quantity ?? ""}
                            onChange={e =>
                                setQuoteData(prev => ({
                                    ...prev,
                                    quantity: e.target.value === "" ? null : Number(e.target.value),
                                }))
                            }
                        />
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Expected Start Date</label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            placeholder="YYYY-MM-DD"
                            value={quoteData.expected_start_date ?? ""}
                            onChange={e => setQuoteData(prev => ({ ...prev, expected_start_date: e.target.value }))}
                        />
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Notes</label>
                        <textarea
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            rows={3}
                            value={quoteData.notes ?? ""}
                            onChange={e => setQuoteData(prev => ({ ...prev, notes: e.target.value }))}
                        />
                    </div>
                </div>

                {error && (
                    <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
                        {error}
                    </div>
                )}

                <div className="flex gap-3">
                    <Button
                        onClick={handleConfirm}
                        disabled={isSubmitting}
                        className="flex-1 bg-purple-600 hover:bg-purple-700"
                    >
                        {isSubmitting ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Sending...
                            </>
                        ) : (
                            <>
                                <Check className="mr-2 h-4 w-4" />
                                Confirm & Send
                            </>
                        )}
                    </Button>
                    <Button
                        onClick={onCancel}
                        disabled={isSubmitting}
                        variant="outline"
                        className="flex-1"
                    >
                        <X className="mr-2 h-4 w-4" />
                        Cancel
                    </Button>
                </div>
            </div>
        </div>
    );
}

