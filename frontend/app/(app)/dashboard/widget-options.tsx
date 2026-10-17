import { FOCUS_RING } from "@/components/ui/button";
import { RadioOption } from "./radio-option";

export type Placement = "bubble" | "inline";
export type BubblePosition = "right" | "left";
export type WidgetTheme = "light" | "dark";

const POSITIONS: { value: BubblePosition; label: string }[] = [
  { value: "right", label: "Bottom right" },
  { value: "left", label: "Bottom left" },
];

const THEMES: { value: WidgetTheme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

type PlacementChoiceProps = {
  placement: Placement;
  onChange: (placement: Placement) => void;
};

export function PlacementChoice({ placement, onChange }: PlacementChoiceProps) {
  return (
    <fieldset className="space-y-3">
      <legend className="mb-3">
        <h2 className="text-h2 text-fg">Placement</h2>
      </legend>
      <RadioOption name="placement" checked={placement === "bubble"} onSelect={() => onChange("bubble")}>
        <span className="font-medium">Chat bubble</span>
        <span className="text-fg-muted">Floating button in the corner. Opens on click.</span>
      </RadioOption>
      <RadioOption name="placement" checked={placement === "inline"} onSelect={() => onChange("inline")}>
        <span className="font-medium">Inline widget</span>
        <span className="text-fg-muted">Embedded inside a container on the page.</span>
      </RadioOption>
    </fieldset>
  );
}

type ThemeChoiceProps = {
  theme: WidgetTheme;
  onChange: (theme: WidgetTheme) => void;
};

export function ThemeChoice({ theme, onChange }: ThemeChoiceProps) {
  return (
    <fieldset>
      <legend className="mb-2 text-ui font-medium text-fg">Theme</legend>
      <div className="flex gap-3">
        {THEMES.map(({ value, label }) => (
          <RadioOption
            key={value}
            name="theme"
            checked={theme === value}
            onSelect={() => onChange(value)}
            className="font-medium"
          >
            {label}
          </RadioOption>
        ))}
      </div>
    </fieldset>
  );
}

type BubbleOptionsProps = {
  color: string;
  onColorChange: (color: string) => void;
  position: BubblePosition;
  onPositionChange: (position: BubblePosition) => void;
};

export function BubbleOptions({ color, onColorChange, position, onPositionChange }: BubbleOptionsProps) {
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="widget-color" className="mb-2 block text-ui font-medium text-fg">
          Button color
        </label>
        <div className="flex items-center gap-3">
          <input
            id="widget-color"
            type="color"
            value={color}
            onChange={(e) => onColorChange(e.target.value)}
            className={`h-11 w-16 cursor-pointer rounded-control border border-line-strong bg-surface p-1 ${FOCUS_RING.canvas}`}
          />
          <code className="font-mono text-ui text-fg-muted">{color}</code>
        </div>
      </div>
      <fieldset>
        <legend className="mb-2 text-ui font-medium text-fg">Position</legend>
        <div className="flex gap-3">
          {POSITIONS.map(({ value, label }) => (
            <RadioOption
              key={value}
              name="position"
              checked={position === value}
              onSelect={() => onPositionChange(value)}
              className="font-medium"
            >
              {label}
            </RadioOption>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

type InlineOptionsProps = {
  targetId: string;
  onTargetIdChange: (targetId: string) => void;
};

export function InlineOptions({ targetId, onTargetIdChange }: InlineOptionsProps) {
  return (
    <div>
      <label htmlFor="inline-target-id" className="mb-2 block text-ui font-medium text-fg">
        Container element ID
      </label>
      <input
        id="inline-target-id"
        type="text"
        value={targetId}
        onChange={(e) => onTargetIdChange(e.target.value.replace(/[^a-zA-Z0-9-_]/g, "-"))}
        aria-describedby="inline-target-id-help"
        className={`h-11 w-64 rounded-control border border-line-strong bg-surface px-3 font-mono text-ui text-fg placeholder:text-fg-faint ${FOCUS_RING.canvas}`}
        placeholder="chat9-widget"
      />
      <p id="inline-target-id-help" className="mt-2 text-caption text-fg-muted">
        ID of the {"<div>"} that will contain the chat widget.
      </p>
    </div>
  );
}
