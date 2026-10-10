import { LANGUAGE_OPTIONS } from "@/app/dashboard/constants";
import { Language } from "@/types/dashboard";

type LanguageToggleProps = {
  language: Language;
  translate: (value: string) => string;
  onChange: (language: Language) => void;
};

// Each language is named in its own script so users can find it whichever
// language the interface is currently in.
const NATIVE_NAMES: Record<Language, string> = { en: "English", zh: "中文" };

export function LanguageToggle({ language, translate, onChange }: LanguageToggleProps) {
  return (
    <div
      role="group"
      aria-label={translate("Language")}
      className="flex h-8 items-center gap-0.5 rounded-md bg-slate-100 p-0.5 dark:bg-slate-800"
    >
      {LANGUAGE_OPTIONS.map((locale) => {
        const isActive = language === locale;
        return (
          <button
            key={locale}
            type="button"
            lang={locale}
            aria-pressed={isActive}
            onClick={() => onChange(locale)}
            className={`h-7 rounded-[5px] px-2.5 text-xs font-medium transition focus-visible:outline-2 focus-visible:outline-blue-600 ${
              isActive
                ? "bg-white text-slate-900 shadow-sm ring-1 ring-slate-900/5 dark:bg-slate-600 dark:text-white"
                : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            {NATIVE_NAMES[locale]}
          </button>
        );
      })}
    </div>
  );
}
