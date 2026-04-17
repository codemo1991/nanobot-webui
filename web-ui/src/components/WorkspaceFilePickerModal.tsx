import { useEffect, useState, useCallback } from 'react'
import { Modal, Tree, Spin, Empty, Typography, message } from 'antd'
import type { TreeDataNode } from 'antd'
import { FolderOutlined, FileOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import type { WorkspaceTreeEntry } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  onInsert: (path: string) => void
}

interface FileTreeNode extends TreeDataNode {
  entry: WorkspaceTreeEntry
}

function WorkspaceFilePickerModal({ open, onClose, onInsert }: Props) {
  const { t } = useTranslation()
  const [treeData, setTreeData] = useState<FileTreeNode[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([])

  const loadChildren = useCallback(async (nodePath?: string): Promise<FileTreeNode[]> => {
    const entries = await api.getWorkspaceTree(nodePath)
    return entries.map((entry: WorkspaceTreeEntry) => ({
      key: entry.absolute_path,
      title: entry.name,
      icon: entry.type === 'dir' ? <FolderOutlined /> : <FileOutlined />,
      isLeaf: entry.type !== 'dir' || !entry.has_children,
      entry,
    }))
  }, [])

  useEffect(() => {
    if (!open) return
    setSelectedPath(null)
    setError(null)
    setExpandedKeys([])
    setLoading(true)
    loadChildren()
      .then(nodes => {
        setTreeData(nodes)
      })
      .catch((e: Error) => {
        setError(e.message || t('chat.loadWorkspaceError'))
      })
      .finally(() => setLoading(false))
  }, [open, loadChildren, t])

  const onLoadData = async ({ key, children }: FileTreeNode) => {
    if (children) return
    try {
      const nodes = await loadChildren(String(key))
      setTreeData((origin) => {
        const loop = (data: FileTreeNode[]): FileTreeNode[] =>
          data.map((item) => {
            if (item.key === key) {
              return { ...item, children: nodes }
            }
            if (item.children) {
              return { ...item, children: loop(item.children as FileTreeNode[]) }
            }
            return item
          })
        return loop(origin)
      })
    } catch (e) {
      message.error(t('chat.loadWorkspaceError'))
      // Stop spinner by setting empty children
      setTreeData((origin) => {
        const loop = (data: FileTreeNode[]): FileTreeNode[] =>
          data.map((item) => {
            if (item.key === key) {
              return { ...item, children: [], isLeaf: true }
            }
            if (item.children) {
              return { ...item, children: loop(item.children as FileTreeNode[]) }
            }
            return item
          })
        return loop(origin)
      })
    }
  }

  const handleOk = () => {
    if (selectedPath) {
      onInsert(selectedPath)
    }
  }

  return (
    <Modal
      title={t('chat.filePickerTitle')}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      okText={t('common.insert')}
      cancelText={t('common.cancel')}
      okButtonProps={{ disabled: !selectedPath }}
      width={560}
    >
      <div style={{ minHeight: 320, maxHeight: 480, overflow: 'auto' }}>
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
            <Spin />
          </div>
        )}
        {!loading && error && (
          <Empty description={error} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
        {!loading && !error && treeData.length === 0 && (
          <Empty description={t('chat.emptyWorkspace')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
        {!loading && !error && treeData.length > 0 && (
          <Tree<FileTreeNode>
            treeData={treeData}
            loadData={onLoadData}
            expandedKeys={expandedKeys}
            onExpand={(keys) => setExpandedKeys(keys)}
            onSelect={(keys) => {
              if (keys.length > 0) {
                setSelectedPath(String(keys[0]))
              } else {
                setSelectedPath(null)
              }
            }}
            showLine
            showIcon
          />
        )}
      </div>
      {selectedPath && (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          {t('chat.selectedPath')}: {selectedPath}
        </Typography.Text>
      )}
    </Modal>
  )
}

export default WorkspaceFilePickerModal
