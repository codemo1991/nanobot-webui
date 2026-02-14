## 解决方案

使用 Ant Design Tabs 的 `destroyInactiveTabPane={false}` 属性，让切换 tab 时保留组件实例而不是卸载。

### 修改文件

**MirrorPage.tsx** - 在 Tabs 组件添加 `destroyInactiveTabPane={false}` 属性：

```tsx
<Tabs
  activeKey={activeTab}
  onChange={setActiveTab}
  items={tabItems}
  className="mirror-tabs"
  size="large"
  centered
  destroyInactiveTabPane={false}  // 新增此行
/>
```

### 方案优点

1. **改动最小** - 只需添加一个属性
2. **无需引入新依赖** - 使用 Ant Design 原生支持
3. **状态完整保留** - 思考状态、消息列表、会话状态等全部保留
4. **性能影响可控** - 组件只是隐藏，不会重复渲染