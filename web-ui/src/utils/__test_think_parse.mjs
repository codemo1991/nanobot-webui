const s = `<redacted_thinking>
用户说"你好"，这是一个简单的问候。我应该友好地回应。
</redacted_thinking>

你好！有什么我可以帮你的吗？`;

const r = /<redacted_thinking>([\s\S]*?)<\/redacted_thinking>/gi;
const m = s.match(r);
console.log('match', m);
const r2 = /<redacted_thinking>([\s\S]*?)<\/think>/gi;
console.log('short close', s.match(r2));

// with backticks around close (bad model output)
const s2 = `<redacted_thinking>
hello
\`</redacted_thinking>\`

world`;
const r3 = /<redacted_thinking>([\s\S]*?)<\/redacted_thinking>/gi;
console.log('with backticks', s2.match(r3));
