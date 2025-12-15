import { LANGUAGE_OPTIONS } from "@/app/dashboard/constants";
import { Language } from "@/types/dashboard";

type LanguageToggleProps = {
  language: Language;
  translate: (value: string) => string;
  onChange: (language: Language) => void;
};

export function LanguageToggle({ language, translate, onChange }: LanguageToggleProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">
        {translate("Language")}
      </span>
      <div className="flex overflow-hidden rounded-full border border-slate-300 dark:border-slate-700">
        {LANGUAGE_OPTIONS.map((locale) => {
          const isActive = language === locale;
          return (
            <button
              key={locale}
              type="button"
              onClick={() => onChange(locale)}
              className={`px-3 py-1 text-xs font-semibold transition ${
                isActive
                  ? "bg-slate-100 text-slate-900"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:bg-slate-700"
              }`}
            >
              {translate(locale === "en" ? "English" : "Chinese")}
            </button>
          );
        })}
      </div>
    </div>
  );
}
