interface ApiErrorProps {
  message: string;
}

export function ApiError({ message }: ApiErrorProps) {
  return (
    <div className="surface-panel border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] p-8 text-center">
      <h2 className="mb-2 text-xl font-semibold text-[var(--tone-danger-text)]">
        Unable to load data
      </h2>
      <p className="text-[var(--text-secondary)]">{message}</p>
    </div>
  );
}
