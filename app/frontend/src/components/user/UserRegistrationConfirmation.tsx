import { useState } from "react";
import { Check, X, Mail, User, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export type UserRegistrationData = {
    customer_name: string;
    contact_info: string;
};

type UserRegistrationConfirmationProps = {
    initialUserData: UserRegistrationData;
    onConfirm: (data?: UserRegistrationData) => Promise<void>;
    onCancel: () => void;
};

export default function UserRegistrationConfirmation({ 
    initialUserData, 
    onConfirm, 
    onCancel 
}: UserRegistrationConfirmationProps) {
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [userData, setUserData] = useState<UserRegistrationData>(initialUserData);

    const handleConfirm = async () => {
        setIsSubmitting(true);
        setError(null);
        try {
            await onConfirm(userData);
            // Don't set isSuccess here - let the parent component handle closing
            // The parent will call setUserRegistrationData(null) which will close this component
            // If we set isSuccess here, it might show success page briefly before closing
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to register user");
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
            <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-2xl font-bold text-gray-900">Confirm Your Information</h2>
                    <button
                        onClick={onCancel}
                        className="text-gray-400 hover:text-gray-600 transition-colors"
                        aria-label="Close"
                    >
                        <X className="h-6 w-6" />
                    </button>
                </div>

                <p className="text-gray-600 mb-6">
                    Please confirm your information so we can provide you with better service.
                </p>

                <div className="space-y-4 mb-6">
                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500 flex items-center gap-2">
                            <User className="h-4 w-4" />
                            Your Name
                        </label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            value={userData.customer_name}
                            onChange={e => setUserData(prev => ({ ...prev, customer_name: e.target.value }))}
                        />
                    </div>

                    <div className="border-b pb-3">
                        <label className="text-sm font-medium text-gray-500 flex items-center gap-2">
                            <Mail className="h-4 w-4" />
                            Email Address
                        </label>
                        <input
                            className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-purple-500"
                            type="email"
                            value={userData.contact_info}
                            onChange={e => setUserData(prev => ({ ...prev, contact_info: e.target.value }))}
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
                        disabled={isSubmitting || !userData.customer_name || !userData.contact_info}
                        className="flex-1 bg-purple-600 hover:bg-purple-700"
                    >
                        {isSubmitting ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Registering...
                            </>
                        ) : (
                            <>
                                <Check className="mr-2 h-4 w-4" />
                                Confirm & Register
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

