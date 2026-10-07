import { ArtifactList } from './pages/ArtifactList.tsx'
import { Editor } from './pages/Editor.tsx'
import { useRoute } from './router.ts'

export function App() {
  const route = useRoute()
  switch (route.page) {
    case 'list':
      return <ArtifactList />
    case 'edit':
      return <Editor id={route.id} />
    default:
      return (
        <div className="p-8 text-sm text-muted">
          Not found.{' '}
          <a href="/" className="text-accent underline">
            Your artifacts
          </a>
        </div>
      )
  }
}
