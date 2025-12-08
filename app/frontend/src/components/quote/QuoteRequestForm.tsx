import { ChangeEvent, FormEvent, useState, useEffect } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

type QuoteFormState = {
    customerName: string;
    contactInfo: string;
    productPackage: string;
    quantity: string;
    startDate: string;
    notes: string;
};

const defaultState: QuoteFormState = {
    customerName: "",
    contactInfo: "",
    productPackage: "",
    quantity: "",
    startDate: "",
    notes: ""
};

type Product = {
    id: string;
    name: string;
};

const QuoteRequestForm = () => {
    const { t } = useTranslation();
    const [formState, setFormState] = useState<QuoteFormState>(defaultState);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [quoteLink, setQuoteLink] = useState<string | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [products, setProducts] = useState<Product[]>([]);
    const [loadingProducts, setLoadingProducts] = useState(true);

    useEffect(() => {
        // Load products from Salesforce
        const loadProducts = async () => {
            try {
                const response = await fetch("/api/products");
                if (response.ok) {
                    const data = await response.json();
                    const productList = data.products || [];
                    console.log("Products loaded:", productList.length, "products");
                    console.log("Product names:", productList.map((p: Product) => p.name));
                    setProducts(productList);
                } else {
                    console.error("Failed to load products: HTTP", response.status);
                    const errorText = await response.text();
                    console.error("Error response:", errorText);
                }
            } catch (error) {
                console.error("Failed to load products:", error);
            } finally {
                setLoadingProducts(false);
            }
        };
        
        loadProducts();
    }, []);

    const onFieldChange = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
        const { name, value } = event.target;
        setFormState(current => ({ ...current, [name]: value }));
    };

    const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        setIsSubmitting(true);
        setErrorMessage(null);

        try {
            const response = await fetch("/api/quotes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    customer_name: formState.customerName,
                    contact_info: formState.contactInfo,
                    product_package: formState.productPackage,
                    quantity: formState.quantity,
                    expected_start_date: formState.startDate,
                    notes: formState.notes
                })
            });

            if (!response.ok) {
                throw new Error(t("quote.error"));
            }

            const payload = await response.json();
            setQuoteLink(payload.quote_url ?? payload.quoteUrl ?? null);
            setFormState(defaultState);
        } catch (error) {
            const message = error instanceof Error ? error.message : t("quote.error");
            setErrorMessage(message);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <section className="w-full max-w-3xl rounded-xl bg-white p-6 shadow-lg">
            <div className="mb-6">
                <h2 className="text-2xl font-semibold text-gray-900">{t("quote.title")}</h2>
                <p className="text-sm text-gray-600">{t("quote.description")}</p>
            </div>

            <form className="space-y-4" onSubmit={handleSubmit}>
                <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="customerName">
                        {t("quote.customerName")}
                    </label>
                    <input
                        id="customerName"
                        name="customerName"
                        className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                        value={formState.customerName}
                        onChange={onFieldChange}
                        required
                    />
                </div>

                <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="contactInfo">
                        {t("quote.contactInfo")} ({t("quote.emailAddress")})
                    </label>
                    <input
                        id="contactInfo"
                        name="contactInfo"
                        type="email"
                        className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                        value={formState.contactInfo}
                        onChange={onFieldChange}
                        placeholder="example@email.com"
                        required
                    />
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                    <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="productPackage">
                            {t("quote.productPackage")}
                        </label>
                        {loadingProducts ? (
                            <div className="w-full rounded-md border border-gray-300 p-2 text-gray-500">
                                {t("quote.loadingProducts")}
                            </div>
                        ) : products.length > 0 ? (
                            <select
                                id="productPackage"
                                name="productPackage"
                                className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                                value={formState.productPackage}
                                onChange={onFieldChange}
                                required
                            >
                                <option value="">{t("quote.selectProduct")}</option>
                                {products.map((product) => (
                                    <option key={product.id} value={product.name}>
                                        {product.name}
                                    </option>
                                ))}
                            </select>
                        ) : (
                            <>
                                <input
                                    id="productPackage"
                                    name="productPackage"
                                    className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                                    value={formState.productPackage}
                                    onChange={onFieldChange}
                                    placeholder={t("quote.productPlaceholder")}
                                    required
                                />
                                <p className="mt-1 text-xs text-gray-500">
                                    {t("quote.noProductsAvailable")}
                                </p>
                            </>
                        )}
                    </div>
                    <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="quantity">
                            {t("quote.quantity")}
                        </label>
                        <input
                            id="quantity"
                            name="quantity"
                            type="number"
                            min="1"
                            className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                            value={formState.quantity}
                            onChange={onFieldChange}
                            required
                        />
                    </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                    <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="startDate">
                            {t("quote.startDate")}
                        </label>
                        <input
                            id="startDate"
                            name="startDate"
                            type="date"
                            className="w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                            value={formState.startDate}
                            onChange={onFieldChange}
                            required
                        />
                    </div>
                    <div>
                        <label className="mb-1 block text-sm font-medium text-gray-700" htmlFor="notes">
                            {t("quote.notes")}
                        </label>
                        <textarea
                            id="notes"
                            name="notes"
                            className="h-24 w-full rounded-md border border-gray-300 p-2 focus:border-purple-500 focus:outline-none"
                            value={formState.notes}
                            onChange={onFieldChange}
                            placeholder={t("quote.notesPlaceholder")}
                        />
                    </div>
                </div>

                <div className="flex items-center gap-4">
                    <Button type="submit" disabled={isSubmitting}>
                        {isSubmitting ? t("quote.submitting") : t("quote.submit")}
                    </Button>
                    {errorMessage && <p className="text-sm text-red-600">{errorMessage}</p>}
                </div>
            </form>

            {quoteLink && (
                <div className="mt-6 rounded-md border border-green-200 bg-green-50 p-4 text-sm text-green-900">
                    <p className="font-medium">{t("quote.successLabel")}</p>
                    <a className="text-purple-600 underline" href={quoteLink} target="_blank" rel="noreferrer">
                        {quoteLink}
                    </a>
                </div>
            )}
        </section>
    );
};

export default QuoteRequestForm;

