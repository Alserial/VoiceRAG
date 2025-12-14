import { useState } from "react";
import { Check, X, Mail, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export type QuoteData = {
    customer_name: string;
    contact_info: string;
    product_package: string;
    quantity: number;
    expected_start_date?: string;
    notes?: string;
};

type QuoteConfirmationProps = {
    quoteData: QuoteData;
    onConfirm: () => Promise<void>;
    onCancel: () => void;
};

export default function QuoteConfirmation({ quoteData, onConfirm, onCancel }: QuoteConfirmationProps) {
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [isSuccess, setIsSuccess] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleConfirm = async () => {
        setIsSubmitting(true);
        setError(null);
        try {
            await onConfirm();
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
                        <p className="text-lg text-gray-900">{quoteData.customer_name}</p>
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Contact Information</label>
                        <p className="text-lg text-gray-900 flex items-center gap-2">
                            <Mail className="h-4 w-4" />
                            {quoteData.contact_info}
                        </p>
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Product/Package</label>
                        <p className="text-lg text-gray-900">{quoteData.product_package}</p>
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500">Quantity</label>
                        <p className="text-lg text-gray-900">{quoteData.quantity}</p>
                    </div>

                    {quoteData.expected_start_date && (
                        <div className="border-b pb-3">
                            <label className="text-sm font-medium text-gray-500">Expected Start Date</label>
                            <p className="text-lg text-gray-900">{quoteData.expected_start_date}</p>
                        </div>
                    )}

                    {quoteData.notes && (
                        <div className="border-b pb-3">
                            <label className="text-sm font-medium text-gray-500">Notes</label>
                            <p className="text-lg text-gray-900">{quoteData.notes}</p>
                        </div>
                    )}
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

